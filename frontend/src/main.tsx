import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./providers/theme";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // 切换界面后 30s 内的缓存视为新鲜，直接复用，不再重复请求后端。
      // 原 5s 过短：离开页面几秒再切回，整页 3-5 个查询全部重发，切页卡顿。
      staleTime: 30_000,
      // 卸载页面的缓存保留 5 分钟，切回来先渲染缓存再后台按需刷新。
      gcTime: 5 * 60_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <I18nProvider><App /></I18nProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
