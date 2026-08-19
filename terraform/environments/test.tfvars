# test 环境变量（测试环境，最小规格）
environment              = "test"
region                   = "cn-shanghai"
rds_instance_class       = "mysql.n2.small.1"
redis_instance_type      = "redis.master.small.default"
ack_worker_instance_type = "ecs.g6.large"
ack_worker_count         = 2
vpc_cidr                 = "10.1.0.0/16" # 环境独立网段，避免环境间互通时路由冲突
