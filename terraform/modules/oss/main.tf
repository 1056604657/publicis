# OSS 对象存储模块
variable "environment" {}
variable "bucket_name" {}

resource "alicloud_oss_bucket" "this" {
  bucket = var.bucket_name
  acl    = "private"
}

output "bucket_name" {
  value = alicloud_oss_bucket.this.bucket
}
