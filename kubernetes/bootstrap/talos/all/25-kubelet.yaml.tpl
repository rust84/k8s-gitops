---
# Kubelet stays on v1alpha1: KubeletConfig has no extraMounts, and OpenEBS hostpath
# (openebs-hostpath StorageClass) needs /var/openebs/local inside the kubelet.
apiVersion: v1alpha1
kind: KubeletConfig
$patch: delete
---
machine:
  kubelet:
    image: ghcr.io/siderolabs/kubelet:{{ .KubernetesVersion }}
    defaultRuntimeSeccompProfileEnabled: true
    disableManifestsDirectory: true
    extraMounts:
      - destination: /var/openebs/local
        type: bind
        source: /var/openebs/local
        options: ["bind", "rshared", "rw"]
---
# Kubelet node IP subnet
apiVersion: v1alpha1
kind: KubeNodeConfig
nodeIP:
  validSubnets:
    - 10.20.0.0/24
