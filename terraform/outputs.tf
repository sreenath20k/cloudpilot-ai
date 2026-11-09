output "aws_region" {
  description = "Region the stack is deployed in."
  value       = var.aws_region
}

output "ecr_repository_url" {
  description = "Push the Docker image here."
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name (used by the chat backend)."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name (used by the chat backend)."
  value       = aws_ecs_service.app.name
}

output "app_url" {
  description = "Public URL of the running service."
  value       = "http://${aws_lb.app.dns_name}"
}

output "health_url" {
  description = "Health check URL."
  value       = "http://${aws_lb.app.dns_name}/health"
}
