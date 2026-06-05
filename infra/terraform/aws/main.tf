locals {
  stack_name = "${var.project}-${var.env}"
}

output "stack_name" {
  value = local.stack_name
}

output "db_host" {
  value = aws_db_instance.pg.address
}

output "db_port" {
  value = 5432
}

output "db_user" {
  value = var.db_user
}

output "db_pass" {
  value     = var.db_pass
  sensitive = true
}

output "db_name" {
  value = var.db_name
}

output "lambda_sg_id" {
  value = aws_security_group.lambda_sg.id
}

output "private_subnet_ids" {
  value = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

