# Blender GPT Assistant

Blender 안에서 GPT 또는 Claude와 대화하고 장면을 조작하는 GPL-3.0-or-later 오픈소스 애드온입니다. **채팅 → Blender 작업 실행 → 결과 확인**이 첫 개발 목표입니다.

공통 애드온의 채팅·AI 연결·장면 조작 기반을 먼저 완성합니다. 산업별 기능은 이 저장소를 포크하거나 클론한 별도 프로젝트에서 확장할 수 있습니다.

## 현재 작업 범위

- Blender의 **GPT** 사이드바에서 채팅, 대화 기록, 요청 취소, 새 대화
- ChatGPT·Claude 계정 로그인 또는 OpenAI·Anthropic API 키 연결
- 큐브·구·원기둥·평면·원뿔 생성
- 선택 객체 이동, 회전, 크기 변경, 이름 변경
- Blender 메인 스레드에서 허용된 작업 실행 및 실행 결과 표시
- Blender 실행 취소로 작업 되돌리기

예시 요청:

- “원점에 큐브 하나 만들어줘.”
- “선택한 객체를 X축으로 2만큼 옮겨줘.”
- “Z축으로 45도 회전해줘.”
- “크기를 두 배로 하고 이름을 Sample로 바꿔줘.”

응답의 작업 명령을 검증한 뒤 실행합니다. 이동은 객체의 로컬 위치에 더하고, 회전은 XYZ 오일러 각도, 크기 변경은 배율로 처리합니다. 지원하지 않는 작업은 대화로 설명하며 임의 Python을 실행하지 않습니다. 메타데이터 ID를 부여하지 않아도 기본 조작을 사용할 수 있습니다.

## 설치와 첫 사용

1. Windows 또는 macOS에 Blender 4.2 이상을 준비합니다. 계정 로그인 방식은 Codex CLI 또는 Claude Code도 필요합니다. API 키 방식에는 해당 CLI가 필요하지 않습니다. 검증 환경은 `VALIDATION.md`를 참고하세요.
2. `python3 scripts/package.py`로 `dist/twin-assistant-0.1.0.zip`을 만듭니다.
3. Blender → Edit → Preferences → Get Extensions → Install from Disk에서 ZIP을 선택합니다.
4. 3D View에서 `N` 키 → **GPT** 탭을 엽니다.
5. 연결 방식을 선택하고 **연결 확인**을 누릅니다. 계정 방식은 기존 CLI 로그인 세션을 재사용하며, 필요한 경우 **로그인**으로 공식 인증 절차를 시작합니다. API 방식은 키를 입력합니다.
6. 조회된 모델을 선택하고 요청을 전송합니다. 기존 객체를 수정할 때는 해당 객체를 선택합니다.
7. 실행 결과를 확인합니다. 작업은 Ctrl+Z로 되돌릴 수 있습니다.

CLI를 찾지 못하면 애드온 Preferences에서 Codex 또는 Claude 실행 파일의 절대 경로를 지정하세요. Windows의 npm Codex 설치는 `codex.cmd`에서 실제 네이티브 실행 파일을 찾습니다. Claude Code는 네이티브 설치를 사용하세요. **연결 닫기**는 애드온의 연결을 종료하며 공용 CLI 계정을 로그아웃시키지 않습니다.

대화 기록은 실행 세션에서만 유지하며 `.blend`에 저장하지 않습니다. AI에는 사용자의 요청과 최근 대화, 작업에 필요한 선택 객체의 이름·유형·변환값을 전달합니다. 형상 전체·텍스처·전체 `.blend` 파일은 보내지 않습니다. 애드온은 비밀번호나 인증 토큰을 저장하지 않습니다.

## 연결 방식

| 방식 | 준비 사항 | 이용 기준 |
|---|---|---|
| ChatGPT 로그인 | Codex CLI와 ChatGPT 로그인 | 계정의 Codex 이용 권한·한도 |
| Claude 로그인 | Claude Code와 Claude 로그인 | 계정의 Claude Code 이용 권한·한도 |
| OpenAI API | OpenAI API 키 | OpenAI API 별도 과금 |
| Claude API | Anthropic API 키 | Anthropic API 별도 과금 |

API 키 입력을 비우면 해당 환경 변수 `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY`를 사용할 수 있습니다. 입력한 키는 현재 Blender 세션의 메모리에만 두며 연결 방식 변경·연결 종료 시 지웁니다. API 키를 `.blend` 또는 Blender 설정에 저장하지 않습니다. 계정 로그인 방식은 환경 변수의 API 키를 자동으로 사용하지 않습니다.

Claude 계정 방식의 모델 목록은 CLI 별칭이며 계정별 이용 가능 모델을 조회한 결과는 아닙니다. 실제 이용 가능 여부는 요청 시 확인됩니다.

애드온은 무료이지만 AI 사용 권한과 과금은 선택한 제공자의 조건을 따릅니다. OpenAI, Anthropic 또는 Blender의 공식 애드온은 아닙니다.

## 이전 메타데이터 프로토타입

보조 패널에 ID 부여, 이름 규칙 분류, ID 기반 CSV 가져오기, 검토 후 적용, JSON 내보내기가 남아 있습니다. 채팅 조작에는 이 기능이나 사전 메타데이터 입력이 필요하지 않습니다.

## 개발·검증

```sh
python3 -m unittest discover -s tests -v
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python scripts/blender_smoke.py
python3 scripts/package.py
```

확인된 동작과 미검증 범위는 [VALIDATION.md](VALIDATION.md)를 참고하세요.

## 라이선스

Copyright (c) 2026 Blender GPT Assistant contributors.

이 프로그램은 **GNU General Public License version 3 또는 그 이후 버전(GPL-3.0-or-later)**에 따라 배포합니다. 전문은 [LICENSE](LICENSE), 이전 프로토타입의 저작권 고지는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요. 어떠한 보증도 제공하지 않습니다.

개발에 참여할 때는 [AGENTS.md](AGENTS.md)와 [CLAUDE.md](CLAUDE.md)를 참고하세요.
