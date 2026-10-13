provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = var.proxmox_insecure
}

# NOTE: placeholder. VMs 101-104 predate IaC. Per ADR-0001, decide between
# `tofu import` of the existing VMs and recreating them from a cloud-init
# template. Sketch of the declarative path:
#
# resource "proxmox_virtual_environment_vm" "k8s_node" {
#   for_each  = var.k8s_nodes
#   name      = each.value.name
#   node_name = "pve"
#   tags      = ["k8s", "tower_cloud"]
#
#   clone {
#     vm_id = var.k8s_template
#   }
#
#   cpu    { cores = each.value.cores }
#   memory { dedicated = each.value.memory }
#
#   disk {
#     datastore_id = "local-lvm"
#     size         = each.value.disk_gb
#   }
#
#   initialization {
#     ip_config {
#       ipv4 {
#         address = each.value.ip
#         gateway = each.value.gateway
#       }
#     }
#   }
# }
