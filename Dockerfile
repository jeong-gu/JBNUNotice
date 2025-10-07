# 1. 기본 이미지 설정
FROM python:3.12-slim

# 2. 시스템 업데이트 및 Cron 설치
# slim-buster 이미지에는 cron이 기본으로 설치되어 있지 않으므로 설치해야 합니다.
# 한국 미러 사이트 사용 예시 (카카오 미러)
RUN echo "deb https://mirror.kakao.com/debian trixie main" > /etc/apt/sources.list && \
    echo "deb https://mirror.kakao.com/debian trixie-updates main" >> /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/debian.sources

# 이후 apt-get update 실행
RUN apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=10 && \
    apt-get install -y cron && \
    rm -rf /var/lib/apt/lists/*

# 3. 작업 디렉토리 설정
WORKDIR /app

# 4. 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 애플리케이션 파일 및 cron 설정 파일 복사
# entrypoint.sh에 실행 권한을 부여해야 합니다.
COPY . .
RUN chmod +x entrypoint.sh

# 6. CMD 대신 ENTRYPOINT를 사용하여 컨테이너의 메인 프로세스로 설정
# 컨테이너가 시작되면 entrypoint.sh 스크립트가 실행됩니다.
ENTRYPOINT ["/app/entrypoint.sh"]

# CMD는 ENTRYPOINT에 넘길 인자로 사용되지만, 여기서는 ENTRYPOINT가 모든 것을 처리하므로 CMD는 생략하거나 비워둡니다.
# CMD ["cron", "-f"] 와 같은 형태로 ENTRYPOINT 없이 CMD만 사용할 수도 있지만, 
# crontab 파일 등록을 위해 entrypoint 스크립트를 사용하는 것이 더 유연합니다.
