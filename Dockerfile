# 1. Базовый образ с Python
FROM python:3.13-slim

# 2. Устанавливаем рабочую директорию
WORKDIR /app

# 3. Эти переменные помогают Python работать лучше внутри Docker
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 4. Копируем только файл с зависимостями
COPY requirements.txt .

# 5. Устанавливаем их
RUN pip install --no-cache-dir -r requirements.txt

# 6. Копируем все остальные файлы проекта (код, картинки и т.д.)
COPY . .

# 7. SRE Best Practice: Создаем папку для постоянных данных
#    И создаем пользователя 'bot' без прав администратора
RUN mkdir -p /data/media && \
    useradd -m -u 1000 bot && \
    chown -R bot:bot /app /data

# 8. Переключаемся на этого пользователя
USER bot

# 9. Запускаем бота, когда контейнер стартует
CMD ["python", "main.py"]