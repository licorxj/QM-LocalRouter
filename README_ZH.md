# LocalRouter

> **你的本地 API 路由管家** | Your Local LLM API Routing Manager

> [English](README.md) | **中文**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://typescriptlang.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

<p align="center">
  <img src="docs/images/%E4%B8%BB%E9%A1%B5.png" alt="LocalRouter 鐣岄潰棰勮" width="800">
</p>

---

## 鏍稿績浜偣

- **鈿?鏈湴涓€绔欏紡绠＄悊** 鈥?闆嗕腑绠＄悊鎵€鏈変笂娓?AI 骞冲彴锛圤penAI銆丄nthropic銆丟oogle Gemini銆丏eepSeek 绛?65+ 骞冲彴锛夛紝鎵€鏈夐厤缃拰鏁版嵁瀛樺偍鏈湴锛屾棤闇€鍏綉鏆撮湶
- **馃攧 澶氱瓥鐣ユ櫤鑳借矾鐢?* 鈥?5 绉嶈礋杞藉潎琛＄瓥鐣ワ紙杞銆佸姞鏉冦€侀殢鏈恒€佹晠闅滆浆绉汇€佷紭鍏堢骇锛? 鑷姩鏁呴殰鍒囨崲
- **馃攽 澶?Key 璐熻浇鍧囪　** 鈥?姣忎釜骞冲彴鍙厤缃涓?API Key锛屾敮鎸?RPM 闃堝€煎拰璁℃暟闃堝€艰嚜鍔ㄥ垏鎹?- **馃 澶?Model 鑷姩璋冨害** 鈥?鏍规嵁绛栫暐瑙勫垯鑷姩閫夋嫨鏈€浼樻ā鍨嬶紝鏀寔妯″瀷绾у埆鑷姩闄嶇骇
- **馃洝锔?鍗忚鑷姩杞崲** 鈥?OpenAI / Claude / Gemini 涓夌鍗忚鑷姩浜掕浆锛屼换鎰?SDK 璋冪敤浠绘剰妯″瀷
- **馃敀 鏁版嵁瀹夊叏** 鈥?绾湰鍦拌繍琛岋紝API Key 浣跨敤 Fernet 鍔犲瘑瀛樺偍锛岄浂鏁版嵁澶栨硠
- **馃寪 灞€鍩熺綉鍏变韩** 鈥?閮ㄧ讲鍚庡眬鍩熺綉鍐呮墍鏈夎澶囧叡浜?API Key 鍜岃矾鐢辩瓥鐣ワ紝鍙渶涓€涓帴鍏ョ偣

---

## 涓轰粈涔堥€夋嫨 LocalRouter锛?
绠＄悊澶氫釜 LLM 骞冲彴闈炲父鐥涜嫤銆傛瘡涓钩鍙伴兘鏈変笉鍚岀殑 API 鏍煎紡銆佽璐规柟寮忓拰閫熺巼闄愬埗銆備綘闇€瑕佺淮鎶ゅ濂?API Key銆佸涓?SDK銆佹墜鍔ㄥ鐞嗘晠闅滆浆绉汇€?
**LocalRouter 閫氳繃涓€涓粺涓€鐨?OpenAI 鍏煎绔偣瑙ｅ喅浜嗚繖浜涢棶棰橈細**

| 闂 | 瑙ｅ喅鏂规 |
|------|----------|
| 澶氫釜骞冲彴 SDK 涓嶇粺涓€ | 鍗曚竴 OpenAI 鍏煎 API |
| API Key 绠＄悊娣蜂贡 | 闆嗕腑瀵嗛挜绠＄悊 + 鍔犲瘑瀛樺偍 |
| 骞冲彴鏁呴殰 | 鑷姩鏁呴殰杞Щ鍒板鐢ㄥ钩鍙?Key |
| 閫熺巼闄愬埗 | 澶?Key 杞崲 + RPM 鏅鸿兘鍒囨崲 |
| 鍗忚涓嶅吋瀹?| 鑷姩杞崲 OpenAI/Claude/Gemini 鏍煎紡 |
| 妯″瀷鍙戠幇鍥伴毦 | 涓€閿粠涓婃父鍚屾 + 鍐呯疆 100+ 宸茬煡妯″瀷搴?|

---

## 馃寪 灞€鍩熺綉缁熶竴绠＄悊浼樺娍

LocalRouter 浠庤璁′箣鍒濆氨涓?*灞€鍩熺綉锛圠AN锛夐儴缃?*鑰屼紭鍖栥€傚紑鍚眬鍩熺綉璁块棶鍚庯紝缃戝唴鎵€鏈夎澶囧叡浜竴涓帴鍏ョ偣锛?
| 浼樺娍 | 璇存槑 |
|------|------|
| **馃挵 鎴愭湰鍏变韩** | 涓€浠?API Key 璁㈤槄鏈嶅姟鏁翠釜鍥㈤槦锛屾棤闇€涓烘瘡鍙拌澶囧崟鐙喘涔?|
| **馃攽 闆嗕腑瀵嗛挜绠＄悊** | 涓€澶勬坊鍔?杞崲 API Key锛屾墍鏈夊眬鍩熺綉璁惧鑷姩鐢熸晥 |
| **馃搵 缁熶竴绛栫暐绠℃帶** | 璺敱瑙勫垯涓€娆″畾涔夛紝鍥㈤槦鎴愬憳鍏变韩鐩稿悓鐨勬櫤鑳借矾鐢辩瓥鐣?|
| **鈿?闆跺欢杩熷紑閿€** | 灞€鍩熺綉杞彂寤惰繜 <1ms锛屾棤闇€缁忚繃浜戠涓浆 |
| **馃敀 鏁版嵁闅愮** | 鎵€鏈?API 璇锋眰鍦ㄥ眬鍩熺綉鍐呭畬鎴愶紝涓嶇粡杩囧閮ㄧ綉鍏?|
| **馃枼锔?璺ㄥ钩鍙版敮鎸?* | Windows銆乵acOS銆丩inux銆乮OS銆丄ndroid 鈥?浠讳綍鏀寔 HTTP 鐨勮澶囬兘鑳界敤 |
| **馃攲 鍗虫彃鍗崇敤** | 鍏煎鎵€鏈?OpenAI SDK 瀹㈡埛绔?鈥?Cursor銆丆hatBox銆丆herryStudio銆丱pen WebUI |

**鍏稿瀷浣跨敤鍦烘櫙锛?*
- 鍥㈤槦宸ヤ綔绔欏叡浜竴濂?API Key锛岀粺涓€绠＄悊
- 绉诲姩璁惧鏃犻渶瀹夎 SDK锛岀洿鎺ヨ皟鐢?LLM API
- IoT 璁惧閫氳繃灞€鍩熺綉绔偣闆嗘垚 AI 鑳藉姏
- CI/CD 娴佹按绾夸娇鐢ㄤ笌寮€鍙戠幆澧冪浉鍚岀殑璺敱绛栫暐

---

## 鍔熻兘鐗规€?
### 骞冲彴绠＄悊
- 缁撴瀯鍖栫鐞嗕笂娓稿钩鍙帮紙鍚嶇О銆佸崗璁€佸湴鍧€銆佸浘鏍囷級
- 涓€閿悳绱㈠浘鏍囷紙鐧惧害/蹇呭簲/鎼滅嫍鍥剧墖锛?- **65+ 鐑棬骞冲彴妯℃澘** 鈥?涓€閿揩閫熸坊鍔?- 鍚敤/绂佺敤鍗曚釜骞冲彴

### API Key 绠＄悊
- 鎸夊钩鍙板垎缁勭鐞?API Key锛屾敮鎸佸埆鍚?- 瀵嗛挜鍋ュ悍妫€娴嬶紙鏈夋晥/鏃犳晥/鏈祴璇曪級
- 鎵归噺娴嬭瘯鍏ㄩ儴 Key銆佹壒閲忓垹闄ゆ棤鏁?Key
- 鏉冮噸璁剧疆锛堢敤浜庡姞鏉冭礋杞藉潎琛★級
- **鍔犲瘑瀛樺偍** 鈥?Fernet 瀵圭О鍔犲瘑锛屽瘑閽ユ枃浠惰嚜鍔ㄧ敓鎴?
### 妯″瀷绠＄悊
- 姣忎釜骞冲彴鐙珛鐨勬ā鍨嬬鐞嗭紝鏀寔璇︾粏灞炴€?- **涓€閿粠涓婃父鍚屾** 鈥?鏅鸿兘鍙傛暟妫€娴嬪拰濉厖
- **鍚屾寮圭獥浜や簰** 鈥?鏀寔鍏ㄩ€夈€佸弽閫夈€侀€愭潯娣诲姞銆佹壒閲忔坊鍔?- **鍐呯疆宸茬煡妯″瀷鏁版嵁搴?* 鈥?100+ 棰勯厤缃ā鍨嬪弬鏁?- 鑷姩妯″瀷绫诲瀷妫€娴嬶紙鏂囨湰/鍥惧儚/瑙嗛/TTS/宓屽叆锛?
### 璺敱绛栫暐

| 绛栫暐 | 璇存槑 |
|------|------|
| **杞 (Round Robin)** | 鎸夐『搴忎緷娆¤疆鎹㈣鍒?|
| **鍔犳潈 (Weighted)** | 鎸夋潈閲嶆瘮渚嬪垎閰嶆祦閲?|
| **闅忔満 (Random)** | 闅忔満閫夋嫨瑙勫垯 |
| **鏁呴殰杞Щ (Failover)** | 鎸変紭鍏堢骇锛屽け璐ユ椂鑷姩鍒囨崲 |
| **浼樺厛绾?(Priority)** | 濮嬬粓浣跨敤鏈€楂樹紭鍏堢骇 |

### Key 绛栫暐

| 绛栫暐 | 璇存槑 |
|------|------|
| **杞** | 鎸夐『搴忚疆鎹娇鐢?Key |
| **闅忔満** | 闅忔満閫夋嫨 Key |
| **鏁呴殰杞Щ** | 浣跨敤绗竴涓?Key锛屽け璐ュ垯鍒囨崲 |
| **RPM 闃堝€?* | 姣忓垎閽熻姹傛暟瓒呴槇鍊兼椂鑷姩鍒囨崲 |
| **璁℃暟闃堝€?* | 鎬昏姹傛暟瓒呴槇鍊兼椂鑷姩鍒囨崲 |

### 鍗忚鏀寔
- **OpenAI** 鈥?鍘熺敓鍏煎锛圙PT 绯诲垪銆乷1/o3銆丏ALL-E銆乀TS銆乄hisper锛?- **Anthropic Claude** 鈥?鑷姩鏍煎紡杞崲锛堜粠 OpenAI 鏍煎紡锛?- **Google Gemini** 鈥?鑷姩鏍煎紡杞崲锛堜粠 OpenAI 鏍煎紡锛?- **鑷畾涔?* 鈥?浠讳綍 OpenAI 鍏煎绔偣

### 鍙娴嬫€?- **浠〃鐩?* 鈥?浠婃棩璇锋眰閲忋€佹垚鍔熺巼銆佸钩鍧囧欢杩熴€乀oken 鐢ㄩ噺
- **璇锋眰鏃ュ織** 鈥?瀹屾暣鐨勬悳绱㈢瓫閫夎姹傚巻鍙诧紙鎸夌瓥鐣?骞冲彴/鐘舵€?鏃堕棿锛?- **绛栫暐娴嬭瘯** 鈥?涓婄嚎鍓嶆祴璇曡矾鐢遍摼璺?
### 绯荤粺璁剧疆
- 鏆楄壊/浜壊涓婚鍒囨崲
- **涓嫳鏂囧弻璇晫闈?*
- **杈撳嚭鍗忚杞崲** 鈥?杩斿洖 OpenAI / Claude / Gemini 鏍煎紡
- **澶囦唤涓庢仮澶?* 鈥?鎵嬪姩/鑷姩澶囦唤锛屾敮鎸佸畾鏃跺拰涓婁紶鎭㈠

---

## 绯荤粺鏋舵瀯

```
瀹㈡埛绔?(OpenAI SDK / 绗笁鏂瑰簲鐢?
  |
  | POST /v1/chat/completions (model="绛栫暐鍚嶇О")
  v
[proxy.py] -- 鏍规嵁妯″瀷鍚嶈В鏋愮瓥鐣?  |
  v
[balancer.py] -- 閫夋嫨璺敱瑙勫垯 + API Key
  |                (5 绉嶈礋杞藉潎琛＄瓥鐣?/ 3 绉?Key 绛栫暐)
  v
[forwarder.py] -- 鍗忚閫傞厤 + 鍙戦€佷笂娓歌姹?  |                (OpenAI / Claude / Gemini)
  v
[protocol_adapter.py] -- 鍙€夎緭鍑烘牸寮忚浆鎹?  |
  v
[stream_handler.py] -- SSE 娴佸紡澶勭悊
  |
  v
瀹㈡埛绔?<--- 鏍囧噯 OpenAI 鏍煎紡鍝嶅簲
```

---

## 蹇€熷紑濮?
### 鐜瑕佹眰

- Python 3.10+
- Node.js 18+

### 1. 鍏嬮殕涓庡垵濮嬪寲

```bash
git clone https://github.com/licorxj/QM-LocalRouter.git
cd ApiRouteManeger

# 鍒濆鍖栨暟鎹簱锛堣嚜鍔ㄥ垱寤鸿櫄鐜銆佸畨瑁呬緷璧栥€佸垱寤烘暟鎹簱锛?cd scripts
python init_db.py        # 璺ㄥ钩鍙?Python 鑴氭湰
# 鎴? ./init_db.sh       # Linux/macOS
# 鎴? init_db.bat        # Windows
cd ..
```

### 2. 鍚姩鏈嶅姟

**鏂瑰紡 A锛氫娇鐢ㄧ鐞嗚剼鏈紙鎺ㄨ崘锛?*

```bash
# Linux/macOS
chmod +x scripts/manage.sh
./scripts/manage.sh start

# Windows
scripts\manage.bat start
```

**鏂瑰紡 B锛氭墜鍔ㄥ惎鍔?*

```bash
# 缁堢 1 - 鍚庣
cd backend
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS
uvicorn app.main:app --host 0.0.0.0 --port 12002

# 缁堢 2 - 鍓嶇
cd frontend
npm install
npm run dev
```

### 3. 浣跨敤

1. 娴忚鍣ㄦ墦寮€ **http://localhost:12001**
2. 杩涘叆 **骞冲彴绠＄悊** 椤甸潰锛岀偣鍑?**+** 娣诲姞骞冲彴锛堟垨浣跨敤 **鐑棬骞冲彴** 涓€閿坊鍔狅級
3. 鍦ㄥ钩鍙颁笅娣诲姞 API Key
4. 娣诲姞妯″瀷 鈥?鍙墜鍔ㄣ€佷竴閿悓姝ャ€佹垨浣跨敤"鍚屾骞冲彴妯″瀷"寮圭獥
5. 杩涘叆 **璺敱绛栫暐**锛屽垱寤虹瓥鐣ュ苟娣诲姞璺敱瑙勫垯
6. 瀹㈡埛绔腑璁剧疆 `base_url` 涓?`http://localhost:12002/v1`锛宍model` 涓虹瓥鐣ュ悕绉?
---

## 鎶€鏈爤

| 灞?| 鎶€鏈?| 璇存槑 |
|-------|-----------|------|
| 鍓嶇 | React 18 + TypeScript + Vite 5 | 鍗曢〉搴旂敤锛屽揩閫熺儹鏇存柊 |
| UI | shadcn/ui + Tailwind CSS 3 | 鐜颁唬鏃犻殰纰嶇粍浠跺簱 |
| 鐘舵€佺鐞?| Zustand + TanStack Query 5 | 杞婚噺鐘舵€?+ 鏈嶅姟绔紦瀛?|
| 鍥介檯鍖?| 鑷畾涔?React Context | 涓枃 / 鑻辨枃 |
| 鍚庣 | Python 3.10+ + FastAPI + uvicorn | 寮傛楂樻€ц兘 API |
| 鏁版嵁搴?| SQLite + SQLAlchemy 2.0 (寮傛) | 闆堕厤缃湰鍦板瓨鍌?|
| 鍔犲瘑 | cryptography (Fernet) | API Key AES-128 鍔犲瘑 |
| HTTP | httpx (寮傛) | 涓婃父 API 璇锋眰杞彂 |

---

## API 姒傝

鎵€鏈変唬鐞嗙鐐逛綅浜?`/v1/`锛屼笌 OpenAI SDK 瀹屽叏鍏煎銆?
| 绔偣 | 璇存槑 |
|------|------|
| `POST /v1/chat/completions` | 鑱婂ぉ琛ュ叏锛堟祦寮?+ 闈炴祦寮忥級 |
| `POST /v1/completions` | 鏂囨湰琛ュ叏 |
| `POST /v1/embeddings` | 鍚戦噺宓屽叆 |
| `POST /v1/images/generations` | 鍥惧儚鐢熸垚 |
| `POST /v1/audio/speech` | 璇煶鍚堟垚 |
| `POST /v1/videos` | 瑙嗛鐢熸垚 |
| `GET /v1/models` | 鍒楀嚭娲昏穬绛栫暐 |

绠＄悊 API 鍦?`/api/` 涓嬶紙Swagger锛歚http://localhost:12002/docs`锛?
---

## 瀹㈡埛绔厤缃?
| 瀹㈡埛绔?| 閰嶇疆鏂瑰紡 |
|--------|----------|
| **OpenAI SDK** | `base_url="http://localhost:12002/v1"`, `api_key="浠绘剰鍊?` |
| **ChatBox** | 璁剧疆 > 鑷畾涔?API > Base URL: `http://localhost:12002/v1` |
| **CherryStudio** | 璁剧疆 > 娣诲姞鑷畾涔?API > Base URL: `http://localhost:12002/v1` |
| **LobeChat** | 璁剧疆 > 娣诲姞鑷畾涔夋ā鍨?> API URL: `http://localhost:12002/v1` |
| **Open WebUI** | `OPENAI_API_BASE_URL=http://localhost:12002/v1` |
| **Cursor** | 璁剧疆 > 妯″瀷 > Base URL: `http://localhost:12002/v1` |
| **curl** | 瑙佷笅鏂圭ず渚?|

```bash
curl http://localhost:12002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"my-strategy","messages":[{"role":"user","content":"浣犲ソ锛?}]}'
```

> **娉ㄦ剰**: `api_key` 鍙～浠绘剰鍊笺€俙model` 瀛楁蹇呴』濉瓥鐣ュ悕绉般€?
---


## 鏂囨。

| 鏂囨。 | 璇存槑 |
|------|------|
| [README.md](README.md) | English documentation |
| [docs/USAGE.md](docs/USAGE.md) | English user guide |
| [docs/USAGE_ZH.md](docs/USAGE_ZH.md) | 涓枃浣跨敤鏁欑▼ |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 閮ㄧ讲鎸囧崡锛圖ocker銆乂PS銆乄indows銆丩inux锛?|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 寮€鍙戣€呮枃妗?|
| [docs/API.md](docs/API.md) | API 鍙傝€?|
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | 渚濊禆娓呭崟 |

---

## 鑱旂郴涓庢敮鎸?
濡傛灉杩欎釜椤圭洰瀵逛綘鏈夊府鍔╋紝娆㈣繋鑱旂郴鍜屼氦娴侊細

<div align="center">
  <table>
    <tr>
      <td align="center">
        <strong>娣诲姞濂藉弸 (寰俊)</strong><br>
        <img src="docs/images/contact-qr.jpg" width="200" alt="鑱旂郴浜岀淮鐮?><br>
        <em>鎵爜娣诲姞濂藉弸</em>
      </td>
      <td align="center">
        <strong>鎵撹祻鏀寔 (寰俊/鏀粯瀹?鏀粯)</strong><br>
        <img src="docs/images/donate-qr.png" width="200" alt="鎵撹祻浜岀淮鐮?><br>
        <em>鎵爜鏀寔椤圭洰</em>
      </td>
    </tr>
  </table>
</div>

---

## 璁稿彲璇?
MIT License
