-- 三层 Hello-World 应用 - 数据库初始化脚本
-- 在 PostgreSQL 启动时执行，创建商品表并插入示例数据

CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 幂等插入：只在表为空时插入示例数据，避免重复初始化
INSERT INTO items (name, description)
SELECT '示例商品1', 'Hello World 项目示例数据 - 验证三层架构'
WHERE NOT EXISTS (SELECT 1 FROM items);

INSERT INTO items (name, description)
SELECT '示例商品2', '用于验证 Redis 缓存与 PostgreSQL 持久化'
WHERE NOT EXISTS (SELECT 1 FROM items WHERE name = '示例商品2');
