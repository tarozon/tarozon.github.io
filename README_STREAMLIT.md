# TAROZON Streamlit MVP

이 저장소의 HTML 기반 타로 페이지를 Streamlit 기반으로 전환하기 위한 **1카드 MVP**입니다.

## 실행 방법 (로컬)

```bash
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

## 포함 기능 (MVP)

- 덱 선택: RWS / Thoth / I Ching / Holitzka
- 스프레드 선택: 1카드 / 3카드(과거-현재-미래)
- 카드: DRAW, FLIP(정/역), 리셋, (옵션) 수동 선택
- 카드 이미지 표시 및 JPG 다운로드
- GPT 리딩 요청문 생성 및 TXT 다운로드

## 데이터 위치

- 덱 정의(JSON): `data/decks/*.json`
- 스프레드 정의(JSON): `data/spreads/*.json`
- 이미지 폴더:
  - RWS: `cards/`
  - Thoth: `thothcards/`
  - I Ching: `iching/`
  - Holitzka: `holitzka/`

