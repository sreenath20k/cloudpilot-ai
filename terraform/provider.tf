# Terraform and AWS provider configuration.
# Credentials are NOT stored here: the AWS provider reads them from
# C:\Users\<you>\.aws\credentials (created by `aws configure`) or environment variables.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Every resource gets these tags, which makes cost tracking and cleanup easy.
  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}
