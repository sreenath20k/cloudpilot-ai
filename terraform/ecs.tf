# ECS: cluster, task definition (what to run) and service (keep N copies running).

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # Container Insights costs extra; basic metrics are enough here
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = var.log_retention_days
}

# Initial task definition (revision 1). The chat interface later registers NEW
# revisions of this same family (e.g. with more CPU/memory) - see the lifecycle note
# on the service below.
resource "aws_ecs_task_definition" "app" {
  family                   = var.project_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # matches images built with Docker Desktop on Windows x64
  }

  container_definitions = jsonencode([{
    name      = var.project_name
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = var.container_port
      protocol      = "tcp"
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = "${var.project_name}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Give a new task time to start before ALB health checks can mark it unhealthy.
  health_check_grace_period_seconds = 30

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true # needed to reach ECR without a NAT Gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.project_name
    container_port   = var.container_port
  }

  # If a new deployment keeps failing (e.g. bad CPU/memory change), ECS stops it
  # and rolls back to the last working revision automatically.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # IMPORTANT - avoiding Terraform drift:
  # The chat interface changes desired_count and task_definition on the RUNNING
  # service. Ignoring them here means a later `terraform plan/apply` will not
  # "fix" those changes back to the Terraform defaults.
  lifecycle {
    ignore_changes = [desired_count, task_definition]
  }

  depends_on = [aws_lb_listener.http]
}
