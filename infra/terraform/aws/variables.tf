variable "project" {
  type    = string
  default = "cloud-migrate-ai"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "db_name" {
  type    = string
  default = "cmadb"
}

variable "db_user" {
  type    = string
  default = "cmadmin"
}

variable "db_pass" {
  type      = string
  sensitive = true
}

variable "cidr" {
  type    = string
  default = "10.50.0.0/16"
}

