"""Web panel for PostBot"""
import json
import logging
from aiohttp import web

from .db import Database

logger = logging.getLogger(__name__)


class WebPanel:
    """Async web panel for managing posts"""
    
    def __init__(self, db: Database, bot_instance):
        self.db = db
        self.bot = bot_instance
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/api/posts', self.get_posts)
        self.app.router.add_get('/api/posts/{pid}', self.get_post)
        self.app.router.add_put('/api/posts/{pid}', self.update_post)
        self.app.router.add_delete('/api/posts/{pid}', self.delete_post)
        self.app.router.add_get('/api/export', self.export_posts)
        self.app.router.add_post('/api/import', self.import_posts)
        self.app.router.add_get('/api/stats', self.get_stats)

    async def _auth(self, req) -> int:
        """Validate token and return user_id or 0"""
        token = req.query.get('token')
        if not token:
            return 0
        user = await self.db.get_user_by_token(token)
        return user[0] if user else 0

    async def index(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.Response(text="Token required. Get link from bot.", status=401)
        
        html = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PostBot Panel</title>
    <style>
        :root {
            --bg: #0f0f1a;
            --card: rgba(255,255,255,0.03);
            --border: rgba(255,255,255,0.08);
            --accent: #6366f1;
            --accent-hover: #818cf8;
            --danger: #ef4444;
            --success: #22c55e;
            --text: #f1f5f9;
            --muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg);
            background-image: 
                radial-gradient(ellipse at top, rgba(99,102,241,0.15) 0%, transparent 50%),
                radial-gradient(ellipse at bottom right, rgba(236,72,153,0.1) 0%, transparent 50%);
            min-height: 100vh;
            color: var(--text);
            padding: 20px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { 
            text-align: center; 
            font-size: 2rem; 
            margin-bottom: 30px; 
            background: linear-gradient(135deg, var(--accent), #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
        }
        .stat-value { font-size: 2rem; font-weight: 700; color: var(--accent); }
        .stat-label { font-size: 0.875rem; color: var(--muted); margin-top: 5px; }
        .actions {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.875rem;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .btn-primary { 
            background: var(--accent); 
            color: white; 
        }
        .btn-primary:hover { background: var(--accent-hover); transform: translateY(-1px); }
        .btn-danger { background: var(--danger); color: white; }
        .btn-danger:hover { opacity: 0.9; }
        .btn-ghost { 
            background: transparent; 
            border: 1px solid var(--border); 
            color: var(--text);
        }
        .btn-ghost:hover { background: var(--card); }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 12px;
            transition: all 0.2s;
        }
        .card:hover { border-color: var(--accent); }
        .post { display: flex; justify-content: space-between; align-items: flex-start; gap: 15px; }
        .post-content { flex: 1; min-width: 0; }
        .post-title { 
            font-weight: 600; 
            margin-bottom: 8px; 
            display: flex; 
            align-items: center; 
            gap: 10px;
        }
        .post-text { 
            color: var(--muted); 
            font-size: 0.875rem; 
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .post-meta { 
            font-size: 0.75rem; 
            color: var(--muted); 
            margin-top: 8px;
            display: flex;
            gap: 15px;
        }
        .post-actions { display: flex; gap: 8px; flex-shrink: 0; }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .badge-active { background: rgba(34,197,94,0.2); color: var(--success); }
        .badge-inactive { background: rgba(239,68,68,0.2); color: var(--danger); }
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.8);
            backdrop-filter: blur(4px);
            z-index: 100;
            align-items: center;
            justify-content: center;
        }
        .modal.show { display: flex; }
        .modal-content {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 30px;
            max-width: 500px;
            width: 90%;
        }
        .modal h2 { margin-bottom: 20px; }
        .form-group { margin-bottom: 15px; }
        .form-label { display: block; margin-bottom: 6px; font-size: 0.875rem; color: var(--muted); }
        .form-input {
            width: 100%;
            padding: 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--card);
            color: var(--text);
            font-size: 1rem;
        }
        .form-input:focus { outline: none; border-color: var(--accent); }
        textarea.form-input { min-height: 120px; resize: vertical; }
        .modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px; }
        #posts { display: grid; gap: 12px; }
        .empty { text-align: center; padding: 60px 20px; color: var(--muted); }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 PostBot Panel</h1>
        
        <div class="stats" id="stats">
            <div class="stat"><div class="stat-value" id="stat-total">-</div><div class="stat-label">Всего постов</div></div>
            <div class="stat"><div class="stat-value" id="stat-active">-</div><div class="stat-label">Активных</div></div>
            <div class="stat"><div class="stat-value" id="stat-sent">-</div><div class="stat-label">Отправлено</div></div>
        </div>
        
        <div class="actions">
            <button class="btn btn-primary" onclick="exportPosts()">📤 Экспорт</button>
            <input type="file" id="importFile" accept=".json" style="display:none" onchange="importPosts(this)">
            <button class="btn btn-primary" onclick="document.getElementById('importFile').click()">📥 Импорт</button>
            <button class="btn btn-ghost" onclick="load()">🔄 Обновить</button>
        </div>
        
        <div id="posts"></div>
    </div>

    <div class="modal" id="editModal">
        <div class="modal-content">
            <h2>✏️ Редактировать пост</h2>
            <input type="hidden" id="editId">
            <div class="form-group">
                <label class="form-label">Текст поста</label>
                <textarea class="form-input" id="editContent"></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">Время (HH:MM)</label>
                <input type="text" class="form-input" id="editTime" placeholder="12:00">
            </div>
            <div class="modal-actions">
                <button class="btn btn-ghost" onclick="closeModal()">Отмена</button>
                <button class="btn btn-primary" onclick="saveEdit()">Сохранить</button>
            </div>
        </div>
    </div>

    <script>
        const token = new URLSearchParams(location.search).get('token');
        const api = path => fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + token);
        
        async function load() {
            const [postsRes, statsRes] = await Promise.all([
                api('/api/posts'),
                api('/api/stats')
            ]);
            const posts = await postsRes.json();
            const stats = await statsRes.json();
            
            document.getElementById('stat-total').textContent = stats.total || 0;
            document.getElementById('stat-active').textContent = stats.active || 0;
            document.getElementById('stat-sent').textContent = stats.sent || 0;
            
            if (!posts.length) {
                document.getElementById('posts').innerHTML = '<div class="empty">Нет постов</div>';
                return;
            }
            
            document.getElementById('posts').innerHTML = posts.map(p => `
                <div class="card post">
                    <div class="post-content">
                        <div class="post-title">
                            <span class="badge ${p.is_active ? 'badge-active' : 'badge-inactive'}">
                                ${p.is_active ? 'Активен' : 'Откл'}
                            </span>
                            <span>#${p.post_id}</span>
                        </div>
                        <div class="post-text">${(p.content || 'Медиа').substring(0, 100)}</div>
                        <div class="post-meta">
                            <span>📅 ${p.schedule_type}</span>
                            <span>⏰ ${p.scheduled_time || '-'}</span>
                            ${p.scheduled_date ? '<span>🗓 ' + p.scheduled_date + '</span>' : ''}
                        </div>
                    </div>
                    <div class="post-actions">
                        <button class="btn btn-ghost" onclick="edit(${p.post_id}, '${escape(p.content || '')}', '${p.scheduled_time || ''}')">✏️</button>
                        <button class="btn btn-danger" onclick="del(${p.post_id})">🗑</button>
                    </div>
                </div>
            `).join('');
        }

        function escape(s) { return s.replace(/'/g, "\\'").replace(/\\n/g, '\\\\n'); }

        async function del(pid) {
            if (!confirm('Удалить пост #' + pid + '?')) return;
            await fetch('/api/posts/' + pid + '?token=' + token, {method: 'DELETE'});
            load();
        }

        function edit(pid, content, time) {
            document.getElementById('editId').value = pid;
            document.getElementById('editContent').value = unescape(content).replace(/\\n/g, '\\n');
            document.getElementById('editTime').value = time;
            document.getElementById('editModal').classList.add('show');
        }

        function closeModal() {
            document.getElementById('editModal').classList.remove('show');
        }

        async function saveEdit() {
            const pid = document.getElementById('editId').value;
            const content = document.getElementById('editContent').value;
            const time = document.getElementById('editTime').value;
            await fetch('/api/posts/' + pid + '?token=' + token, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content, scheduled_time: time})
            });
            closeModal();
            load();
        }

        async function exportPosts() {
            const res = await api('/api/export');
            const data = await res.json();
            const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'posts_export.json';
            a.click();
        }

        async function importPosts(input) {
            const file = input.files[0];
            if (!file) return;
            const text = await file.text();
            await fetch('/api/import?token=' + token, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: text
            });
            alert('Импортировано!');
            load();
        }

        load();
    </script>
</body>
</html>'''
        return web.Response(text=html, content_type='text/html')

    async def get_posts(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response([], status=401)
        posts = await self.db.get_posts(uid, limit=100)
        return web.json_response([{
            "post_id": p.post_id,
            "content": p.content,
            "is_active": p.is_active,
            "schedule_type": p.schedule_type,
            "scheduled_time": p.scheduled_time,
            "scheduled_date": p.scheduled_date
        } for p in posts])

    async def get_post(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        pid = int(req.match_info['pid'])
        post = await self.db.get_post(pid)
        if not post or post.owner_id != uid:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({
            "post_id": post.post_id,
            "content": post.content,
            "media_type": post.media_type,
            "schedule_type": post.schedule_type,
            "scheduled_time": post.scheduled_time,
            "scheduled_date": post.scheduled_date,
            "is_active": post.is_active,
            "pin_post": post.pin_post,
            "has_spoiler": post.has_spoiler,
            "has_participate_button": post.has_participate_button
        })

    async def update_post(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        pid = int(req.match_info['pid'])
        post = await self.db.get_post(pid)
        if not post or post.owner_id != uid:
            return web.json_response({"error": "not found"}, status=404)
        data = await req.json()
        updates = {}
        if "content" in data:
            updates["content"] = data["content"]
        if "scheduled_time" in data:
            updates["scheduled_time"] = data["scheduled_time"]
        if "is_active" in data:
            updates["is_active"] = int(data["is_active"])
        if updates:
            await self.db.update_post(pid, **updates)
        return web.json_response({"updated": pid})

    async def delete_post(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        pid = int(req.match_info['pid'])
        post = await self.db.get_post(pid)
        if not post or post.owner_id != uid:
            return web.json_response({"error": "not found"}, status=404)
        await self.db.delete_post(pid)
        return web.json_response({"deleted": pid})

    async def export_posts(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        data = await self.db.export_posts(uid)
        return web.json_response(data)

    async def import_posts(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        data = await req.json()
        chats = await self.db.get_chats(uid)
        if not chats:
            return web.json_response({"error": "no chats"}, status=400)
        chat_id = chats[0].chat_id
        count = 0
        for p in data:
            await self.db.add_post(
                chat_id=chat_id, owner_id=uid, content=p.get('content', ''),
                media_type=p.get('media_type'), schedule_type=p.get('schedule_type', 'instant'),
                scheduled_time=p.get('scheduled_time', ''), scheduled_date=p.get('scheduled_date'),
                days_of_week=p.get('days_of_week'), day_of_month=p.get('day_of_month'),
                pin_post=p.get('pin_post', 0), has_spoiler=p.get('has_spoiler', 0),
                has_participate=p.get('has_participate', 0), button_text=p.get('button_text', 'Участвовать'),
                url_buttons=p.get('url_buttons', '[]')
            )
            count += 1
        return web.json_response({"imported": count})

    async def get_stats(self, req):
        uid = await self._auth(req)
        if not uid:
            return web.json_response({"error": "unauthorized"}, status=401)
        total = await self.db.count_posts(uid)
        active = await self.db.count_posts(uid, "active")
        stats = await self.db.get_stats(uid)
        return web.json_response({
            "total": total,
            "active": active,
            "sent": stats.posts_sent if stats else 0
        })
