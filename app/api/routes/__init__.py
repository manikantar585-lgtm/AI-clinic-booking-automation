from app.api.routes import admin, chat, health, public, whatsapp

routers = [health.router, public.router, chat.router, admin.router, whatsapp.router]
