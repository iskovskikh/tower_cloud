provider "twc" {
  token = var.twc_token
}

data "twc_dns_zone" "metoda_cc" {
  name = "metoda.cc"
}

locals {
  adminvps_ip = "78.17.99.16"

  a_records = {
    "@"     = "apex"
    "www"   = "www"
    "cloud" = "OpenCloud"
    "auth"  = "Keycloak"
    "*"     = "wildcard routing"
  }
}

resource "twc_dns_rr" "a" {
  for_each = local.a_records

  zone_id = data.twc_dns_zone.metoda_cc.id
  name    = each.key
  type    = "A"
  value   = local.adminvps_ip
}
