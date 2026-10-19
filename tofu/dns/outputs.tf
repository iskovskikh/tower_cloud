output "zone_id" {
  description = "Timeweb Cloud DNS zone id for metoda.cc."
  value       = data.twc_dns_zone.metoda_cc.id
}

output "a_record_ids" {
  description = "Created A record ids, keyed by host label."
  value       = { for name, rr in twc_dns_rr.a : name => rr.id }
}
