#!/bin/bash

# trap "exit" INT TERM
# trap "kill 0" EXIT

export REPO_ROOT=$(git rev-parse --show-toplevel)
export REPLIACS="0 1 2"

need() {
    which "$1" &>/dev/null || die "Binary '$1' is missing but required"
}

need "vault"
need "kubectl"
need "sed"
need "jq"

. "$REPO_ROOT"/setup/.env

message() {
  echo -e "\n######################################################################"
  echo "# $1"
  echo "######################################################################"
}

kvault() {
  name="secrets/$(dirname "$@")/$(basename -s .txt "$@")"
  echo "Writing $name to vault"
  if output=$(envsubst < "$REPO_ROOT/cluster/$*"); then
    printf '%s' "$output" | vault kv put "$name" values.yaml=-
  fi
}

initVault() {
  message "initializing and unsealing vault (if necesary)"
  VAULT_READY=1
  while [ $VAULT_READY != 0 ]; do
    kubectl -n kube-system wait --for condition=Initialized pod/vault-0 > /dev/null 2>&1
    VAULT_READY="$?"
    if [ $VAULT_READY != 0 ]; then
      echo "waiting for vault pod to be somewhat ready..."
      sleep 10;
    fi
  done
  sleep 2

  VAULT_READY=1
  while [ $VAULT_READY != 0 ]; do
    init_status=$(kubectl -n kube-system exec "vault-0" -- vault status -format=json 2>/dev/null | jq -r '.initialized')
    if [ "$init_status" == "false" ] || [ "$init_status" == "true" ]; then
      VAULT_READY=0
    else
      echo "vault pod is almost ready, waiting for it to report status"
      sleep 5
    fi
  done

  sealed_status=$(kubectl -n kube-system exec "vault-0" -- vault status -format=json 2>/dev/null | jq -r '.sealed')
  init_status=$(kubectl -n kube-system exec "vault-0" -- vault status -format=json 2>/dev/null | jq -r '.initialized')

  if [ "$init_status" == "false" ]; then
    echo "initializing vault"
    vault_init=$(kubectl -n kube-system exec "vault-0" -- vault operator init -format json -recovery-shares=1 -recovery-threshold=1) || exit 1
    export VAULT_RECOVERY_TOKEN=$(echo $vault_init | jq -r '.recovery_keys_b64[0]')
    export VAULT_ROOT_TOKEN=$(echo $vault_init | jq -r '.root_token')
    echo "VAULT_RECOVERY_TOKEN is: $VAULT_RECOVERY_TOKEN"
    echo "VAULT_ROOT_TOKEN is: $VAULT_ROOT_TOKEN"

    # sed -i operates differently in OSX vs linux
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "darwin system"
        sed -i '' "s~VAULT_ROOT_TOKEN=\".*\"~VAULT_ROOT_TOKEN=\"$VAULT_ROOT_TOKEN\"~" "$REPO_ROOT"/setup/.env
        sed -i '' "s~VAULT_RECOVERY_TOKEN=\".*\"~VAULT_RECOVERY_TOKEN=\"$VAULT_RECOVERY_TOKEN\"~" "$REPO_ROOT"/setup/.env
    else
        echo "non-darwin (linux?) system"
        sed -i'' "s~VAULT_ROOT_TOKEN=\".*\"~VAULT_ROOT_TOKEN=\"$VAULT_ROOT_TOKEN\"~" "$REPO_ROOT"/setup/.env
        sed -i'' "s~VAULT_RECOVERY_TOKEN=\".*\"~VAULT_RECOVERY_TOKEN=\"$VAULT_RECOVERY_TOKEN\"~" "$REPO_ROOT"/setup/.env
    fi
    echo "SAVE THESE VALUES!"

  REPLIACS_LIST=($REPLIACS)
    echo "sleeping 10 seconds to allow first vault to be ready"
    sleep 10
  for replica in "${REPLIACS_LIST[@]:1}"; do
    echo "joining pod vault-${replica} to raft cluster"
    kubectl -n kube-system exec "vault-${replica}" -- vault operator raft join http://vault-0.vault-internal:8200 || exit 1
  done

    FIRST_RUN=0
  fi

  if [ "$sealed_status" == "true" ]; then
    echo "unsealing vault"
    for replica in $REPLIACS; do
      echo "unsealing vault-${replica}"
      kubectl -n kube-system exec "vault-${replica}" -- vault operator unseal "$VAULT_RECOVERY_TOKEN" || exit 1
    done
  fi
}

portForwardVault() {
  message "port-forwarding vault"
  kubectl -n kube-system port-forward svc/vault 8200:8200 >/dev/null 2>&1 &
  export VAULT_FWD_PID=$!

  sleep 5
}

loginVault() {
  message "logging into vault"
  if [ -z "$VAULT_ROOT_TOKEN" ]; then
    echo "VAULT_ROOT_TOKEN is not set! Check $REPO_ROOT/setup/.env"
    exit 1
  fi

  vault login -no-print "$VAULT_ROOT_TOKEN" || exit 1

  vault auth list >/dev/null 2>&1
  if [[ "$?" -ne 0 ]]; then
    echo "not logged into vault!"
    echo "1. port-forward the vault service (e.g. 'kubectl -n kube-system port-forward svc/vault 8200:8200 &')"
    echo "2. set VAULT_ADDR (e.g. 'export VAULT_ADDR=http://localhost:8200')"
    echo "3. login: (e.g. 'vault login <some token>')"
    exit 1
  fi
}

setupVaultSecretsOperator() {
  message "configuring vault for vault-secrets-operator"
  vault secrets enable -path=secrets -version=2 kv

  # create read-only policy for kubernetes
  cat <<EOF | vault policy write vault-secrets-operator -
  path "secrets/data/*" {
    capabilities = ["read"]
  }
EOF

  export VAULT_SECRETS_OPERATOR_NAMESPACE=$(kubectl -n kube-system get sa vault-secrets-operator -o jsonpath="{.metadata.namespace}")
  export VAULT_SECRET_NAME=$(kubectl -n kube-system get sa vault-secrets-operator -o jsonpath="{.secrets[*]['name']}")
  export SA_JWT_TOKEN=$(kubectl -n kube-system get secret $VAULT_SECRET_NAME -o jsonpath="{.data.token}" | base64 --decode; echo)
  export SA_CA_CRT=$(kubectl -n kube-system get secret $VAULT_SECRET_NAME -o jsonpath="{.data['ca\.crt']}" | base64 --decode; echo)
  export K8S_HOST=$(kubectl -n kube-system config view --minify -o jsonpath='{.clusters[0].cluster.server}')

  # Verify the environment variables
  # env | grep -E 'VAULT_SECRETS_OPERATOR_NAMESPACE|VAULT_SECRET_NAME|SA_JWT_TOKEN|SA_CA_CRT|K8S_HOST'

  vault auth enable kubernetes

  # Tell Vault how to communicate with the Kubernetes cluster
  vault write auth/kubernetes/config \
    token_reviewer_jwt="$SA_JWT_TOKEN" \
    kubernetes_host="$K8S_HOST" \
    kubernetes_ca_cert="$SA_CA_CRT" \
    disable_iss_validation=true #See issue here https://github.com/external-secrets/kubernetes-external-secrets/issues/721

  # Create a role named, 'vault-secrets-operator' to map Kubernetes Service Account to Vault policies and default token TTL
  vault write auth/kubernetes/role/vault-secrets-operator \
    bound_service_account_names="vault-secrets-operator" \
    bound_service_account_namespaces="$VAULT_SECRETS_OPERATOR_NAMESPACE" \
    policies=vault-secrets-operator \
    ttl=24h
}

loadSecretsToVault() {
  message "writing secrets to vault"
  vault kv put secrets/flux/fluxcloud slack_url="$SLACK_WEBHOOK_URL"
  vault kv put secrets/cert-manager/cloudflare-api-key api-key="$CF_API_KEY"

  ####################
  # helm chart values
  ####################
  kvault "default/bitwarden/bitwarden-helm-values.txt"
  kvault "kube-system/external-dns/external-dns-helm-values.txt"
  kvault "kube-system/kured/kured-helm-values.txt"
  kvault "kube-system/vault/vault-helm-values.txt"
  kvault "kube-system/dex/dex-helm-values.txt"
  kvault "kube-system/dex/dex-k8s-authenticator-helm-values.txt"
  kvault "kube-system/oauth2-proxy/oauth2-proxy-helm-values.txt"
  kvault "monitoring/botkube/botkube-helm-values.txt"
  kvault "monitoring/chronograf/chronograf-helm-values.txt"
  kvault "monitoring/kube-prometheus-stack/kube-prometheus-stack-helm-values.txt"
  kvault "monitoring/grafana/grafana-helm-values.txt"
  kvault "monitoring/uptimerobot-prometheus/uptimerobot-helm-values.txt"
  kvault "monitoring/thanos/thanos-helm-values.txt"
  kvault "default/emqx/emqx-helm-values.txt"
  kvault "default/goldilocks/goldilocks-helm-values.txt"
  kvault "default/home-assistant/home-assistant-helm-values.txt"
  kvault "default/minio/minio-helm-values.txt"
  kvault "default/node-red/node-red-helm-values.txt"
  kvault "default/nzbget/nzbget-helm-values.txt"
  kvault "default/radarr/radarr-helm-values.txt"
  kvault "default/sonarr/sonarr-helm-values.txt"
  kvault "default/unifi/unifi-helm-values.txt"
  kvault "velero/velero/velero-helm-values.txt"
  kvault "default/nzbhydra/nzbhydra-helm-values.txt"
  kvault "default/overseerr/overseerr-helm-values.txt"
  kvault "default/organizr/organizr-helm-values.txt"
  kvault "default/tautulli/tautulli-helm-values.txt"
  kvault "default/monica/monica-helm-values.txt"
  kvault "default/valheim/valheim-helm-values.txt"
  kvault "logs/loki/loki-helm-values.txt"
  kvault "default/frigate/frigate-helm-values.txt"
  kvault "default/zigbee2mqtt/zigbee2mqtt-helm-values.txt"
  kvault "default/esphome/esphome-helm-values.txt"
}

FIRST_RUN=1
export KUBECONFIG="$REPO_ROOT/setup/kubeconfig"
export VAULT_ADDR='http://127.0.0.1:8200'

initVault
portForwardVault
loginVault
if [ $FIRST_RUN == 0 ]; then
  setupVaultSecretsOperator
fi
loadSecretsToVault

kill $VAULT_FWD_PID