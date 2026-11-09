# CloudPilot AI - Project Status

Assignment: ReGoBs Technologies - "System Test: Deploy a Service on AWS ECS".
Deadline: Tuesday 10:00 AM IST.

## Key decisions
- Region: **ap-southeast-2 (Sydney)**. The AWS account uses AWS's "projects" sign-up, whose
  service control policies only allow the project's home region (+ us-east-1, us-west-2).
- Public service on ECS Fargate behind an ALB; public subnets, **no NAT Gateway**.
- Chat control plane (FastAPI `/chat` + Streamlit) runs **locally** with the developer's AWS
  credentials. The public container has `CONTROL_API_ENABLED` unset, so it only serves `/` and `/health`.
- Terraform ignores `desired_count` and `task_definition` on the ECS service, so chat changes
  do not cause drift.
- Deterministic parser (no LLM); every change is validated and needs confirmation.

## Live resources (Terraform outputs)
- App URL: http://cloudpilot-ai-alb-1346621927.ap-southeast-2.elb.amazonaws.com
- ECR: 042104538275.dkr.ecr.ap-southeast-2.amazonaws.com/cloudpilot-ai
- Cluster / service: cloudpilot-ai-cluster / cloudpilot-ai-service

## Checklist
- [x] FastAPI service with GET /health (local test)
- [x] Dockerfile, image built
- [x] Image pushed to ECR
- [x] Terraform: VPC, subnets, ECS cluster, service, ALB applied
- [x] Public URL /health returns 200
- [x] Chat backend: parser, validation, ECS manager, API
- [x] Streamlit chat UI
- [x] All tests pass on Windows (`python -m pytest tests -q` -> 68 passed)
- [x] Chat changes verified against the live service (scale + CPU/memory)
- [x] Rebuild/push image with final code, force new deployment (public `/` shows control_api: False)
- [x] README.md
- [ ] GitHub repository created and pushed (with approval)
- [ ] After review: `terraform destroy` (with approval)
