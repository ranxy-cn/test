FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
# 国内镜像源加速 npm 安装（境外亦可正常访问）
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
