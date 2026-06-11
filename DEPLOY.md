# Reverse Prompt Studio — 一键部署

## 方式一：GitHub Pages（推荐，零成本）

### 1. 创建仓库

```bash
# 在 GitHub 上新建一个空仓库（不要勾选 README）
# 然后在本地执行：
git init
git add reverse-prompt-v2-multi.html
git commit -m "init"
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

### 2. 开启 Pages

```
GitHub 仓库 → Settings → Pages → 选 branch: main, 目录: / (root) → Save
等 1-2 分钟，访问：
  https://你的用户名.github.io/你的仓库名/reverse-prompt-v2-multi.html
```

> **不用 HTTPS 证书、不用买服务器、不用装任何东西。**

---

## 方式二：Vercel（更快，自动 HTTPS）

```bash
# 浏览器打开 https://vercel.com
# 用 GitHub 登录 → Add New → Project
# 导入刚建的仓库 → Deploy
# 完事，Vercel 会自动分配一个 https://xxx.vercel.app 地址
```

优势：国内部分地区访问速度比 GitHub Pages 快。

---

## 方式三：本地 Python 一行命令（局域网可用）

```bash
# 进入文件所在目录
cd /Volumes/Data/Desktop/stock_screener

# Python 3 起一个 HTTP 服务
python3 -m http.server 8080
```

然后局域网内所有人访问：

```
http://你的局域网IP:8080/reverse-prompt-v2-multi.html
```

> 这样 CORS 问题就解决了，`file://` 打开才有的拦截不会出现。

---

## 方式四：Flask 集成（如果你已有 Flask 项目）

把文件放到 `templates/` 目录，然后在 `app.py` 加一个路由：

```python
@app.route("/prompt")
def reverse_prompt():
    return render_template("reverse-prompt-v2-multi.html")
```

访问 `http://127.0.0.1:5000/prompt` 即可。

---

## 使用前需要对方准备的

| 项目 | 哪里拿 |
|---|---|
| API Key | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) 或你用的服务商 |
| Endpoint | 默认 `https://api.openai.com/v1`，用第三方改这里 |
| Model | `gpt-4o-mini`（便宜够用）、`gpt-4o`（更准）、或其他兼容模型的名称 |

配置入口：页面右上角 **⚙ 按钮**。

---

## 一句话总结

> **上传 → 配 Key → 点分析 → 收工。**
