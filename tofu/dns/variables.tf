variable "twc_token" {
  description = "Timeweb Cloud API token. NEVER commit; pass via TF_VAR_twc_token."
  type        = string
  sensitive   = true
}
