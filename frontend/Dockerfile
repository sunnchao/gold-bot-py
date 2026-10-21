# syntax=docker/dockerfile:1
# 生产镜像只跑 nginx,静态资源来自 npm run build。

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
# dist 与架构无关，由构建上下文提供（CI 只构建一次）。
COPY dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
