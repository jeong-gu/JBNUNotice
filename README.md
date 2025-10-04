## 이 프로젝트는 전북대학교(JBNU) 컴퓨터인공지능학부생의 공지사항 크롤링 자동화 툴입니다.

- 여러 사이트의 공지사항을 매번 확인하는 것이 번거로워 만들었습니다.
- 한번에 확인하고, 추가적으로 게시물이 게시될 때 바로 확인하기 위해 메일 전송 방식을 택했습니다.

## 확인 가능한 사이트는 아래와 같습니다.

- https://www.jbnu.ac.kr/
- https://csai.jbnu.ac.kr
- https://swuniv.jbnu.ac.kr

## 사용방법

- .env.example 양식으로 .env파일을 작성해야합니다.
- SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO를 수정해야합니다.
- SMTP_PASS는  Gmail 앱 비밀번호(2단계 인증 후 발급)를 입력해야 합니다.
- MAIL_TO에 여러 메일 주소를 추가하여 여러 대상에게 메일을 보낼 수 있습니다.

## cron 매크로 설정 명령어(ex. 4시간마다 프로그램 실행)
- crontab -e

- 0 */4 * * * cd /home/ubuntu/JBNUNotice && /home/ubuntu/JBNUNotice/venv/bin/python3 notice_mailer.py >> /home/ubuntu/JBNUNotice/cron.log 2>&1