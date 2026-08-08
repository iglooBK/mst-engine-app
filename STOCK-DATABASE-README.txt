MST Engine 靜態台股資料庫

檔案：stock-list.js
版本：2026-08-08

每筆資料格式：
{ code: "2330", name: "台積電", market: "TWSE", type: "STOCK" }

market：
TWSE = 上市
TPEx = 上櫃

type：
STOCK = 股票
ETF = ETF

未來更新方式：
1. 只修改 STOCK_LIST 陣列。
2. 新增股票/ETF時填入 code、name、market、type。
3. 不要修改後面的索引與 lookup 函式。
4. 修改後把 index.html、stock-list.js、service-worker.js 一起上傳 GitHub。
5. 若瀏覽器仍顯示舊資料，重新整理或清除 PWA 快取。

目前這份資料是靜態快照，目的是讓「代碼→名稱→市場/類型」不依賴即時 API。
