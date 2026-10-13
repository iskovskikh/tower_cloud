output "k8s_nodes" {
  description = "Node name -> management IP (feeds the Ansible inventory)."
  value       = { for k, v in var.k8s_nodes : v.name => v.ip }
}
