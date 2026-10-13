variable "proxmox_endpoint" {
  description = "Proxmox VE API endpoint."
  type        = string
  default     = "https://192.168.30.201:8006/"
}

variable "proxmox_insecure" {
  description = "Skip TLS verification of the Proxmox API (self-signed cert)."
  type        = bool
  default     = true
}

# Pass via env: TF_VAR_proxmox_api_token='user@realm!tokenid=secret'
variable "proxmox_api_token" {
  description = "Proxmox API token. NEVER commit; provide via TF_VAR_ or a password manager."
  type        = string
  sensitive   = true
}

variable "k8s_template" {
  description = "Proxmox VMID of the Ubuntu cloud-init template to clone."
  type        = number
  default     = 9000
}

variable "k8s_nodes" {
  description = "Kubernetes node VMs (mirrors today's k8s-node-1..4)."
  type = map(object({
    vmid    = number
    name    = string
    ip      = string # static CIDR, e.g. 10.10.120.101/24
    gateway = string
    cores   = number
    memory  = number # MB
    disk_gb = number
  }))
  default = {
    node-1 = { vmid = 101, name = "k8s-node-1", ip = "10.10.120.101/24", gateway = "10.10.120.1", cores = 2, memory = 4096, disk_gb = 40 }
    node-2 = { vmid = 102, name = "k8s-node-2", ip = "10.10.120.102/24", gateway = "10.10.120.1", cores = 2, memory = 4096, disk_gb = 40 }
    node-3 = { vmid = 103, name = "k8s-node-3", ip = "10.10.120.103/24", gateway = "10.10.120.1", cores = 2, memory = 4096, disk_gb = 40 }
    node-4 = { vmid = 104, name = "k8s-node-4", ip = "10.10.120.104/24", gateway = "10.10.120.1", cores = 2, memory = 4096, disk_gb = 40 }
  }
}
