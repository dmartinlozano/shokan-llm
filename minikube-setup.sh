#!/bin/bash
set -e

OS=$(uname -s)
REQUIRED_MEM_GB=16

# ── System resources ──────────────────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
    num_cpus=$(( ($(sysctl -n hw.ncpu) + 1) / 2 ))
    mem_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
elif [[ "$OS" == "Linux" ]]; then
    num_cpus=$(( ($(nproc) + 1) / 2 ))
    mem_gb=$(( $(grep MemTotal /proc/meminfo | awk '{print $2}') / 1024 / 1024 ))
else
    echo "❌ Unsupported OS: $OS"
    exit 1
fi

if [[ $mem_gb -lt $((REQUIRED_MEM_GB * 2)) ]]; then
    echo "❌ Only ${mem_gb}GB RAM detected. At least $((REQUIRED_MEM_GB * 2))GB required."
    exit 1
fi
echo "✅ System: ${mem_gb}GB RAM, $((num_cpus * 2)) CPUs (using ${num_cpus})"

# ── Package installation: macOS ───────────────────────────────────────────────
install_macos() {
    if ! command -v brew &>/dev/null; then
        echo "❌ Homebrew is required: https://brew.sh"
        exit 1
    fi
    brew update
    local tools=(kubectl helm colima docker age minikube)
    for tool in "${tools[@]}"; do
        if ! command -v "$tool" &>/dev/null; then
            echo "📦 Installing $tool..."
            brew install "$tool"
        else
            echo "✅ $tool already installed"
        fi
    done
}

# ── Package installation: Linux ───────────────────────────────────────────────
_install_kubectl_apt() {
    echo "📦 Installing kubectl..."
    curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key \
        | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
    echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.32/deb/ /' \
        | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
    sudo apt-get update -q && sudo apt-get install -y kubectl
}

_install_kubectl_dnf() {
    echo "📦 Installing kubectl..."
    cat <<EOF | sudo tee /etc/yum.repos.d/kubernetes.repo >/dev/null
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.32/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.32/rpm/repodata/repomd.xml.key
EOF
    sudo "$PKG_MGR" install -y kubectl
}

_install_helm() {
    echo "📦 Installing helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
}

_install_docker() {
    echo "📦 Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo systemctl enable docker --now
    if ! groups "$USER" | grep -q docker; then
        sudo usermod -aG docker "$USER"
        echo "⚠️  Added $USER to docker group — re-login may be required."
    fi
}

_install_age() {
    echo "📦 Installing age..."
    if [[ "$PKG_MGR" == "apt-get" ]]; then
        sudo apt-get install -y age 2>/dev/null && return 0
    elif [[ "$PKG_MGR" == "dnf" || "$PKG_MGR" == "yum" ]]; then
        sudo "$PKG_MGR" install -y age 2>/dev/null && return 0
    fi
    # Fallback: GitHub release binary
    local ver="v1.2.0"
    local tmp; tmp=$(mktemp -d)
    curl -fsSL "https://github.com/FiloSottile/age/releases/download/$ver/age-$ver-linux-amd64.tar.gz" \
        | tar -xz -C "$tmp"
    sudo mv "$tmp/age/age" "$tmp/age/age-keygen" /usr/local/bin/
    rm -rf "$tmp"
}

_install_minikube() {
    echo "📦 Installing minikube..."
    local tmp; tmp=$(mktemp)
    curl -fsSLo "$tmp" https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    sudo install "$tmp" /usr/local/bin/minikube
    rm "$tmp"
}

install_linux() {
    if command -v apt-get &>/dev/null; then
        PKG_MGR="apt-get"
        sudo apt-get update -q
    elif command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
    elif command -v yum &>/dev/null; then
        PKG_MGR="yum"
    else
        echo "❌ Unsupported Linux distribution."
        echo "   Please install manually: kubectl helm docker age minikube"
        exit 1
    fi

    command -v kubectl  &>/dev/null || { [[ "$PKG_MGR" == "apt-get" ]] && _install_kubectl_apt  || _install_kubectl_dnf; }
    command -v helm     &>/dev/null || _install_helm
    command -v docker   &>/dev/null || _install_docker
    command -v age      &>/dev/null || _install_age
    command -v minikube &>/dev/null || _install_minikube

    for tool in kubectl helm docker age minikube; do
        command -v "$tool" &>/dev/null && echo "✅ $tool" || { echo "❌ $tool not found"; exit 1; }
    done
}

# ── Docker runtime setup ──────────────────────────────────────────────────────
setup_docker_macos() {
    local current_mem_gb=0
    if colima status -e -j >/dev/null 2>&1; then
        local mem_bytes
        mem_bytes=$(colima status -e -j | grep -o '"memory":[0-9]*' | cut -d':' -f2)
        current_mem_gb=$((mem_bytes / 1024 / 1024 / 1024))
    fi

    if ! colima status >/dev/null 2>&1; then
        echo "🐳 Starting Colima (${REQUIRED_MEM_GB}GB RAM, ${num_cpus} CPUs)..."
        colima start --cpu $num_cpus --memory $REQUIRED_MEM_GB --runtime docker
    elif [[ "$current_mem_gb" -ne "$REQUIRED_MEM_GB" ]]; then
        echo "🔄 Restarting Colima: ${current_mem_gb}GB → ${REQUIRED_MEM_GB}GB..."
        colima stop
        colima start --cpu $num_cpus --memory $REQUIRED_MEM_GB --runtime docker
    else
        echo "✅ Colima running with ${current_mem_gb}GB RAM"
    fi
    docker context use colima
}

setup_docker_linux() {
    if ! systemctl is-active --quiet docker 2>/dev/null; then
        echo "🐳 Starting Docker..."
        sudo systemctl start docker
    else
        echo "✅ Docker is running"
    fi
}

# ── Minikube ──────────────────────────────────────────────────────────────────
start_minikube() {
    if minikube status >/dev/null 2>&1; then
        echo "🛑 Stopping existing Minikube cluster..."
        minikube stop
    fi

    echo "🚀 Starting Minikube ($(( REQUIRED_MEM_GB - 1 ))GB RAM, ${num_cpus} CPUs)..."
    minikube start \
        --memory=$(( REQUIRED_MEM_GB - 1 ))g \
        --cpus=$num_cpus \
        --driver=docker \
        --kubernetes-version=v1.32.0 \
        --cache-images=true \
        --disable-driver-mounts \
        --extra-config=kubelet.cgroup-driver=systemd

    minikube addons enable storage-provisioner
    minikube addons enable default-storageclass
    minikube addons enable ingress
}

# ── Main ──────────────────────────────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
    install_macos
    setup_docker_macos
else
    install_linux
    setup_docker_linux
fi

start_minikube

echo ""
echo "✅ Minikube ready. Next step:"
echo "   bash installer/install.sh"
echo ""
