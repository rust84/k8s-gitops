#!/bin/bash

# nodes
K3S_MASTER="k3s-a"
K3S_WORKERS_AMD64="k3s-b k3s-c k3s-d"
K3S_WORKERS_RPI="pi4-a pi4-b pi4-c"
K3S_VERSION="v1.18.6+k3s1"

REPO_ROOT=$(git rev-parse --show-toplevel)

need() {
    which "$1" &>/dev/null || die "Binary '$1' is missing but required"
}

need "curl"
need "ssh"
need "kubectl"
need "helm"
need "fluxctl"

message() {
  echo -e "\n######################################################################"
  echo "# $1"
  echo "######################################################################"
}

k3sMasterNode() {
  message "installing k3s master to $K3S_MASTER"
  ssh -o "StrictHostKeyChecking=no" ubuntu@"$K3S_MASTER" "curl -sLS https://get.k3s.io | INSTALL_K3S_EXEC='server --tls-san $K3S_MASTER --no-deploy servicelb --no-deploy traefik --flannel-backend host-gw' INSTALL_K3S_VERSION='$K3S_VERSION' sh -"
  ssh -o "StrictHostKeyChecking=no" ubuntu@"$K3S_MASTER" "sudo cat /etc/rancher/k3s/k3s.yaml | sed 's/server: https:\/\/127.0.0.1:6443/server: https:\/\/$K3S_MASTER:6443/'" > "$REPO_ROOT/setup/kubeconfig"
  NODE_TOKEN=$(ssh -o "StrictHostKeyChecking=no" ubuntu@"$K3S_MASTER" "sudo cat /var/lib/rancher/k3s/server/node-token")
}

ks3amd64WorkerNodes() {
  NODE_TOKEN=$(ssh -o "StrictHostKeyChecking=no" ubuntu@"$K3S_MASTER" "sudo cat /var/lib/rancher/k3s/server/node-token")
  for node in $K3S_WORKERS_AMD64; do
    message "joining amd64 $node to $K3S_MASTER"
    EXTRA_ARGS=""
    ssh -o "StrictHostKeyChecking=no" ubuntu@"$node" "curl -sfL https://get.k3s.io | K3S_URL=https://k3os-a:6443 K3S_TOKEN=$NODE_TOKEN INSTALL_K3S_VERSION='$K3S_VERSION' sh -s - $EXTRA_ARGS"
  done
}

ks3armWorkerNodes() {
  NODE_TOKEN=$(ssh -o "StrictHostKeyChecking=no" ubuntu@"$K3S_MASTER" "sudo cat /var/lib/rancher/k3s/server/node-token")
  for node in $K3S_WORKERS_RPI; do
    message "joining pi4 $node to $K3S_MASTER"
    EXTRA_ARGS=""
    ssh -o "StrictHostKeyChecking=no" ubuntu@"$node" "curl -sfL https://get.k3s.io | K3S_URL=https://k3os-a:6443 K3S_TOKEN=$NODE_TOKEN INSTALL_K3S_VERSION='$K3S_VERSION' sh -s - --node-taint arm=true:NoExecute --data-dir /mnt/usb/var/lib/rancher $EXTRA_ARGS"
  done
}

installFlux() {
  message "installing flux"
  # install flux
  kubectl create ns flux
  helm repo add fluxcd https://charts.fluxcd.io

  kubectl apply -f https://raw.githubusercontent.com/fluxcd/helm-operator/master/deploy/crds.yaml
  kubectl apply -f https://raw.githubusercontent.com/coreos/prometheus-operator/release-0.38/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml

  fluxctl install --git-user=rust84 \
  --git-email=rust84@users.noreply.github.com \
  --git-url=git@github.com:rust84/k8s-gitops \
  --namespace flux | kubectl apply -f -

  helm upgrade -i helm-operator fluxcd/helm-operator \
  --version 1.2.0 \
  --namespace flux \
  --set 'createCRD=false' \
  --set 'git.ssh.secretName=flux-git-deploy' \
  --set 'helm.versions=v3' \
  --set 'chartsSyncInterval=5m' \
  --set 'statusUpdateInterval=5m' \
  --set 'prometheus.enabled=true' \
  --set 'prometheus.serviceMonitor.create=true' \
  --set 'prometheus.serviceMonitor.interval=30s' \
  --set 'prometheus.serviceMonitor.scrapeTimeout=10s' \
  --set 'prometheus.serviceMonitor.namespace=flux' \
  --set 'dashboards.enabled=true'

  FLUX_READY=1
  while [ $FLUX_READY != 0 ]; do
    echo "waiting for flux pod to be fully ready..."
    kubectl -n flux wait --for condition=available deployment/flux
    FLUX_READY="$?"
    sleep 5
  done

  # grab output the key
  FLUX_KEY=$(fluxctl --k8s-fwd-ns flux identity)

  message "adding the key to github automatically"
  "$REPO_ROOT"/setup/add-repo-key.sh "$FLUX_KEY"
}

k3sMasterNode
ks3amd64WorkerNodes
ks3armWorkerNodes

export KUBECONFIG="$REPO_ROOT/setup/kubeconfig"
installFlux
"$REPO_ROOT"/setup/bootstrap-objects.sh

# bootstrap vault
"$REPO_ROOT"/setup/bootstrap-vault.sh

message "all done!"
kubectl get nodes -o=wide
