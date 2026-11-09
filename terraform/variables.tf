variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  # ap-southeast-2 (Sydney) is this AWS project's home region. Accounts created with
  # AWS's "projects" sign-up only allow their home region (+ us-east-1, us-west-2).
  default = "ap-southeast-2"
}

variable "project_name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "cloudpilot-ai"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for the public subnets (one per Availability Zone; an ALB needs at least two)."
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "container_port" {
  description = "Port the FastAPI container listens on."
  type        = number
  default     = 8000
}

variable "image_tag" {
  description = "Tag of the image in ECR to deploy."
  type        = string
  default     = "latest"
}

variable "task_cpu" {
  description = "Initial Fargate task CPU units (256 = 0.25 vCPU). Later changes are made by the chat interface."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Initial Fargate task memory in MiB. Later changes are made by the chat interface."
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Initial number of running tasks. Later changes are made by the chat interface."
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps container logs (short = cheaper)."
  type        = number
  default     = 3
}
