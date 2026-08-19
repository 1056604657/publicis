# staging 环境变量（预发布，模拟生产规格）
environment              = "staging"
region                   = "cn-shanghai"
rds_instance_class       = "mysql.n2.large.1"
redis_instance_type      = "redis.master.stand.default"
ack_worker_instance_type = "ecs.g6.xlarge"
ack_worker_count         = 3
vpc_cidr                 = "10.3.0.0/16" # 环境独立网段，避免环境间互通时路由冲突
