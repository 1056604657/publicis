// 前端逻辑：调后端 API，展示商品列表 + 新增商品
// 通过 Nginx 反向代理 /api 到后端，避免跨域

async function loadItems() {
    const sourceEl = document.getElementById('source');
    const itemsEl = document.getElementById('items');
    const errorEl = document.getElementById('error');
    errorEl.textContent = '';

    try {
        const res = await fetch('/api/items');
        const data = await res.json();

        // 显示数据来源（cache 还是 database，验证缓存是否生效）
        sourceEl.textContent = `数据来源：${data.source === 'cache' ? 'Redis 缓存' : 'PostgreSQL 数据库'}`;

        if (data.items.length === 0) {
            itemsEl.innerHTML = '<p>暂无商品</p>';
            return;
        }

        itemsEl.innerHTML = data.items.map(item => `
            <div class="item">
                <h3>${item.name}</h3>
                <p class="desc">${item.description || ''}</p>
                <p class="time">ID: ${item.id} | 创建时间: ${item.created_at}</p>
            </div>
        `).join('');
    } catch (err) {
        errorEl.textContent = `加载失败：${err.message}`;
    }
}

document.getElementById('add-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('name').value;
    const description = document.getElementById('description').value;
    const errorEl = document.getElementById('error');
    errorEl.textContent = '';

    try {
        const res = await fetch('/api/items', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || '新增失败');
        }
        document.getElementById('name').value = '';
        document.getElementById('description').value = '';
        loadItems();
    } catch (err) {
        errorEl.textContent = `新增失败：${err.message}`;
    }
});

// 页面加载时拉取列表
loadItems();
