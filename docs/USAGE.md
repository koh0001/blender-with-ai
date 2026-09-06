# Blender with AI — Usage

## 한국어

### 설치

1. [Releases](https://github.com/koh0001/blender-with-ai/releases)에서 ZIP을 다운로드합니다.
2. Blender 4.2 이상에서 `Edit → Preferences → Get Extensions → Install from Disk`를 선택합니다.
3. 다운로드한 ZIP을 선택하고 Blender를 재시작합니다.

### 시작

1. 3D Viewport에 마우스를 둔 뒤 `N` 키를 누릅니다.
2. 오른쪽 사이드바에서 **AI** 탭을 엽니다.
3. ChatGPT/Claude 로그인 또는 API 키 연결을 선택하고 **연결 확인**을 누릅니다.
4. 모델을 선택하고 요청을 입력한 뒤 **보내기**를 누릅니다.

### 예시

- `원점에 큐브 하나 만들어줘`
- `방금 만든 큐브를 X축으로 2만큼 옮겨줘`
- `선택한 객체를 Z축으로 45도 회전해줘`
- `크기를 두 배로 하고 이름을 Sample로 바꿔줘`

이전 요청과 실행 결과는 다음 요청에 전달됩니다. 후속 작업에는 **선택 객체 함께 보내기**를 켜세요. 생성 이름이 이미 있으면 `.001`, `.002` 형식으로 자동 변경됩니다. 작업은 `Ctrl+Z`로 되돌릴 수 있습니다.

## English

### Install

1. Download the ZIP from [Releases](https://github.com/koh0001/blender-with-ai/releases).
2. In Blender 4.2 or later, open `Edit → Preferences → Get Extensions → Install from Disk`.
3. Select the ZIP and restart Blender.

### Start

1. Move the mouse over the 3D Viewport and press `N`.
2. Open the **AI** tab in the right sidebar.
3. Choose ChatGPT/Claude login or an API key, then click **Check Connection**.
4. Select a model, enter a request, and click **Send**.

### Examples

- `Create a cube at the origin`
- `Move the cube you just created 2 units on X`
- `Rotate the selected object 45 degrees around Z`
- `Double its size and rename it Sample`

Previous requests and execution results are sent with the next request. Keep **Include selected objects** enabled for follow-up scene edits. Duplicate creation names receive Blender-style suffixes such as `.001` and `.002`. Use `Ctrl+Z` to undo a completed operation.

## 日本語

### インストール

1. [Releases](https://github.com/koh0001/blender-with-ai/releases) から ZIP をダウンロードします。
2. Blender 4.2 以降で `Edit → Preferences → Get Extensions → Install from Disk` を開きます。
3. ZIP を選択して Blender を再起動します。

### 使用開始

3D Viewport にマウスを置いて `N` を押し、右側の **AI** タブを開きます。ChatGPT/Claude ログインまたは API キーを選択して **接続確認** を押し、モデルを選んで依頼を送信します。

以前の依頼と実行結果は次の依頼に引き継がれます。続けてオブジェクトを編集する場合は **選択オブジェクトを含める** を有効にしてください。同じ名前で生成すると `.001`、`.002` のように自動で名前が変わります。`Ctrl+Z` で元に戻せます。

## 简体中文

### 安装

1. 从 [Releases](https://github.com/koh0001/blender-with-ai/releases) 下载 ZIP。
2. 在 Blender 4.2 或更高版本中打开 `Edit → Preferences → Get Extensions → Install from Disk`。
3. 选择 ZIP 并重启 Blender。

### 开始使用

将鼠标放在 3D Viewport 上并按 `N`，打开右侧的 **AI** 标签。选择 ChatGPT/Claude 登录或 API 密钥，点击 **连接确认**，选择模型后发送请求。

之前的请求和执行结果会传递给下一次请求。连续编辑场景时请保持 **同时发送选中的对象**。如果创建时名称重复，会自动使用 `.001`、`.002` 等后缀。使用 `Ctrl+Z` 撤销操作。
