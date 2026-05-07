#!/bin/bash
set -e

OS=$(uname -s)
if [[ "$OS" != "Darwin" && "$OS" != "Linux" ]]; then
    echo "❌ Unsupported OS: $OS"
    exit 1
fi

# ── Common tool installation ──────────────────────────────────────────────────

_brew_install() {
    command -v "$1" &>/dev/null && { echo "✅ $1 already installed"; return 0; }
    echo "📦 Installing $1..."
    brew install "${2:-$1}"
}

_apt_install() {
    command -v "$1" &>/dev/null && { echo "✅ $1 already installed"; return 0; }
    echo "📦 Installing $1..."
    sudo apt-get install -y "$1"
}

_detect_pkg_mgr() {
    if   command -v apt-get &>/dev/null; then PKG_MGR="apt-get"
    elif command -v dnf     &>/dev/null; then PKG_MGR="dnf"
    elif command -v yum     &>/dev/null; then PKG_MGR="yum"
    else echo "❌ Unsupported Linux distribution."; exit 1
    fi
}

_install_kubectl_linux() {
    command -v kubectl &>/dev/null && { echo "✅ kubectl already installed"; return 0; }
    echo "📦 Installing kubectl..."
    if [[ "$PKG_MGR" == "apt-get" ]]; then
        curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key \
            | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
        echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.32/deb/ /' \
            | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
        sudo apt-get update -q && sudo apt-get install -y kubectl
    else
        cat <<EOF | sudo tee /etc/yum.repos.d/kubernetes.repo >/dev/null
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.32/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.32/rpm/repodata/repomd.xml.key
EOF
        sudo "$PKG_MGR" install -y kubectl
    fi
}

_install_helm_linux() {
    command -v helm &>/dev/null && { echo "✅ helm already installed"; return 0; }
    echo "📦 Installing helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
}

_install_age_linux() {
    command -v age &>/dev/null && { echo "✅ age already installed"; return 0; }
    echo "📦 Installing age..."
    sudo "$PKG_MGR" install -y age 2>/dev/null && return 0
    local ver="v1.2.0"
    local tmp; tmp=$(mktemp -d)
    curl -fsSL "https://github.com/FiloSottile/age/releases/download/$ver/age-$ver-linux-amd64.tar.gz" \
        | tar -xz -C "$tmp"
    sudo mv "$tmp/age/age" "$tmp/age/age-keygen" /usr/local/bin/
    rm -rf "$tmp"
}

install_common_tools() {
    echo ""
    echo "🔧 Installing common tools (kubectl, helm, age)..."
    if [[ "$OS" == "Darwin" ]]; then
        command -v brew &>/dev/null || { echo "❌ Homebrew required: https://brew.sh"; exit 1; }
        brew update -q
        _brew_install kubectl
        _brew_install helm
        _brew_install age
    else
        _detect_pkg_mgr
        [[ "$PKG_MGR" == "apt-get" ]] && sudo apt-get update -q
        _install_kubectl_linux
        _install_helm_linux
        _install_age_linux
    fi
}

# ── Provider selection ────────────────────────────────────────────────────────

select_provider() {
    echo ""
    echo "Select your Kubernetes provider:"
    echo "  [1] AKS     — Azure Kubernetes Service"
    echo "  [2] GKE     — Google Kubernetes Engine"
    echo "  [3] EKS     — Amazon Elastic Kubernetes Service"
    echo "  [4] Generic — Self-managed, K3s, RKE, on-premise"
    echo ""
    read -rp "Provider (1-4): " sel
    case $sel in
        1) PROVIDER="aks"     ;;
        2) PROVIDER="gke"     ;;
        3) PROVIDER="eks"     ;;
        4) PROVIDER="generic" ;;
        *) echo "❌ Invalid selection."; exit 1 ;;
    esac
}

# ── AKS ───────────────────────────────────────────────────────────────────────

_install_az() {
    command -v az &>/dev/null && { echo "✅ az already installed"; return 0; }
    echo "📦 Installing Azure CLI..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install azure-cli
    else
        curl -fsSL https://aka.ms/InstallAzureCLIDeb | sudo bash
    fi
}

_install_kubelogin() {
    command -v kubelogin &>/dev/null && { echo "✅ kubelogin already installed"; return 0; }
    echo "📦 Installing kubelogin..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install Azure/kubelogin/kubelogin
    else
        local ver
        ver=$(curl -fsSL https://api.github.com/repos/Azure/kubelogin/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
        local tmp; tmp=$(mktemp -d)
        curl -fsSLo "$tmp/kubelogin.zip" \
            "https://github.com/Azure/kubelogin/releases/download/$ver/kubelogin-linux-amd64.zip"
        unzip -q "$tmp/kubelogin.zip" -d "$tmp"
        sudo mv "$tmp/bin/linux_amd64/kubelogin" /usr/local/bin/
        rm -rf "$tmp"
    fi
}

setup_aks() {
    _install_az
    _install_kubelogin

    echo ""
    read -rp "Azure Subscription (leave blank to use current): " AKS_SUBSCRIPTION
    read -rp "Resource Group: " AKS_RESOURCE_GROUP
    read -rp "AKS Cluster name: " AKS_CLUSTER_NAME

    echo ""
    echo "🔐 Logging in to Azure..."
    az login --only-show-errors

    if [[ -n "$AKS_SUBSCRIPTION" ]]; then
        az account set --subscription "$AKS_SUBSCRIPTION"
    fi

    echo "⚙️  Fetching AKS credentials..."
    az aks get-credentials \
        --resource-group "$AKS_RESOURCE_GROUP" \
        --name "$AKS_CLUSTER_NAME" \
        --overwrite-existing

    echo "🔑 Converting kubeconfig for kubelogin..."
    kubelogin convert-kubeconfig -l azurecli
}

# ── GKE ───────────────────────────────────────────────────────────────────────

_install_gcloud() {
    command -v gcloud &>/dev/null && { echo "✅ gcloud already installed"; return 0; }
    echo "📦 Installing Google Cloud SDK..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install --cask google-cloud-sdk
    else
        curl -fsSL https://sdk.cloud.google.com | bash -s -- --disable-prompts
        source "$HOME/google-cloud-sdk/path.bash.inc"
    fi
}

setup_gke() {
    _install_gcloud

    echo ""
    read -rp "GCP Project ID: " GKE_PROJECT
    read -rp "GKE Cluster name: " GKE_CLUSTER_NAME
    read -rp "Region or Zone (e.g. europe-west1 or europe-west1-b): " GKE_LOCATION

    echo ""
    echo "🔐 Logging in to Google Cloud..."
    gcloud auth login --quiet
    gcloud config set project "$GKE_PROJECT"

    echo "📦 Installing gke-gcloud-auth-plugin..."
    gcloud components install gke-gcloud-auth-plugin --quiet

    echo "⚙️  Fetching GKE credentials..."
    gcloud container clusters get-credentials "$GKE_CLUSTER_NAME" \
        --region "$GKE_LOCATION" \
        --project "$GKE_PROJECT"
}

# ── EKS ───────────────────────────────────────────────────────────────────────

_install_aws() {
    command -v aws &>/dev/null && { echo "✅ aws already installed"; return 0; }
    echo "📦 Installing AWS CLI v2..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install awscli
    else
        local tmp; tmp=$(mktemp -d)
        curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "$tmp/awscliv2.zip"
        unzip -q "$tmp/awscliv2.zip" -d "$tmp"
        sudo "$tmp/aws/install"
        rm -rf "$tmp"
    fi
}

setup_eks() {
    _install_aws

    echo ""
    read -rp "AWS Region (e.g. eu-west-1): " EKS_REGION
    read -rp "EKS Cluster name: " EKS_CLUSTER_NAME
    read -rp "AWS Profile (leave blank for default): " AWS_PROFILE_INPUT

    echo ""
    echo "🔐 Verifying AWS credentials..."
    if [[ -n "$AWS_PROFILE_INPUT" ]]; then
        export AWS_PROFILE="$AWS_PROFILE_INPUT"
    fi
    aws sts get-caller-identity

    echo "⚙️  Fetching EKS credentials..."
    aws eks update-kubeconfig \
        --region "$EKS_REGION" \
        --name "$EKS_CLUSTER_NAME" \
        ${AWS_PROFILE_INPUT:+--profile "$AWS_PROFILE_INPUT"}
}

# ── Generic ───────────────────────────────────────────────────────────────────

setup_generic() {
    echo ""
    echo "ℹ️  Generic mode: expects kubectl already configured."
    echo "   If you need to set a kubeconfig, run:"
    echo "   export KUBECONFIG=/path/to/your/kubeconfig"
    echo ""
    read -rp "Press Enter to continue with current context, or Ctrl+C to abort..."
}

# ── Ingress controller ───────────────────────────────────────────────────────

install_ingress_nginx() {
    echo ""
    echo "🔌 Installing nginx ingress controller..."

    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
    helm repo update ingress-nginx &>/dev/null

    helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
        --namespace ingress-nginx \
        --create-namespace \
        --wait \
        --timeout 5m

    echo "⏳ Waiting for external IP assignment..."
    local timeout=120
    for ((i=0; i<timeout; i+=5)); do
        local ip hostname
        ip=$(kubectl get svc ingress-nginx-controller -n ingress-nginx \
            -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
        hostname=$(kubectl get svc ingress-nginx-controller -n ingress-nginx \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
        if [[ -n "$ip" ]]; then
            echo "✅ Ingress controller IP: $ip  →  base domain: ${ip}.nip.io"
            return 0
        elif [[ -n "$hostname" ]]; then
            echo "✅ Ingress controller hostname: $hostname"
            echo "   (EKS/GKE may use hostname instead of IP — BASE_DNS will be set accordingly)"
            return 0
        fi
        sleep 5
    done
    echo "⚠️  External IP not yet assigned. Run 'kubectl get svc -n ingress-nginx' to check."
}

# ── Cluster verification ──────────────────────────────────────────────────────

verify_cluster() {
    echo ""
    echo "🔍 Verifying cluster connection..."
    local context
    context=$(kubectl config current-context)
    echo "   Context: $context"

    if ! kubectl cluster-info &>/dev/null; then
        echo "❌ Cannot connect to cluster. Check your credentials and try again."
        exit 1
    fi
    echo "✅ Cluster reachable"

    echo ""
    echo "🔍 Checking default StorageClass..."
    local default_sc
    default_sc=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null)
    if [[ -z "$default_sc" ]]; then
        echo "⚠️  No default StorageClass found."
        echo "   installer/install.sh requires persistent volumes."
        echo "   Set a default StorageClass before continuing:"
        echo "   kubectl patch storageclass <name> -p '{\"metadata\":{\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'"
    else
        echo "✅ Default StorageClass: $default_sc"
    fi

    echo ""
    echo "🔍 Checking cluster nodes..."
    kubectl get nodes
}

# ── Main ──────────────────────────────────────────────────────────────────────

install_common_tools
select_provider

case $PROVIDER in
    aks)     setup_aks     ;;
    gke)     setup_gke     ;;
    eks)     setup_eks     ;;
    generic) setup_generic ;;
esac

verify_cluster
install_ingress_nginx

echo ""
echo "✅ Cluster ready. Next step:"
echo "   bash installer/install.sh"
echo ""
