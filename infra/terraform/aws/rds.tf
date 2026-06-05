resource "aws_db_subnet_group" "db" {
  name       = "${var.project}-${var.env}-dbsubnet"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]

  tags = {
    Name = "${var.project}-${var.env}-dbsubnet"
  }
}

resource "aws_db_instance" "pg" {
  identifier                 = "${var.project}-${var.env}-pg"
  engine                     = "postgres"
  engine_version             = "16.6" # Choose a supported version for your region, or check using AWS Console
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  db_subnet_group_name       = aws_db_subnet_group.db.name
  vpc_security_group_ids     = [aws_security_group.rds_sg.id]
  username                   = var.db_user
  password                   = var.db_pass
  db_name                    = var.db_name
  storage_encrypted          = true
  skip_final_snapshot        = true
  publicly_accessible        = false
  deletion_protection        = false
  auto_minor_version_upgrade = true
  backup_retention_period    = 1

  tags = {
    Name = "${var.project}-${var.env}-pg"
  }
}

