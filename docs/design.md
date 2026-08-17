
# Design System: Industrial Archive (工业归档)

## 核心哲学 (Core Philosophy)
本设计系统旨在为开发者创造一种**“精密纸质档案”**的视觉体验。它跳出了现代 UI 的圆角和渐变，回归到 80 年代 Unix 技术手册、早期工作站界面以及工业打印报告的冷静与严谨。

- **高密度 (High Density)**：最大化信息展示效率，适合复杂的数据分析和系统监控场景。
- **机器美学 (Machine Aesthetics)**：通过 1px 细线、等宽字体和 ASCII 风格的符号，强调工具的生产力属性。
- **触感设计 (Tactile Design)**：通过低饱和度的米白基底模拟高质感纸张，减少长时间开发的视觉疲劳。

---

## 色彩体系 (Color Palette)

### 基础材质 (Base Materials)
- **Surface (纸张基底)**: `#FCF9F8` - 模拟温润的复古纸张色。
- **Surface Dim (暗色基底)**: `#DCD9D9` - 用于侧边栏或次要容器的背景。
- **Outline (骨架线条)**: `#1A1A1A` - 所有的分割线和边框统一使用 1px 实线，无圆角。

### 功能色 (Functional Colors)
- **Primary (工业蓝)**: `#0047BB` - 用于主按钮、激活态导航和关键标识。
- **Secondary (警示橙)**: `#E65100` - 用于告警状态（WARNING）或强调数据。
- **Success (稳定绿)**: `#2E7D32` - 用于正常运行（NOMINAL）状态。
- **On-Surface (文字主色)**: `#1A1A1A` - 极高对比度的正文黑色。
- **On-Surface Variant (次要文字)**: `#606060` - 用于页脚标签、时间戳等次要信息。

---

## 字体与排版 (Typography)

### 字体系列 (Typeface)
- **Primary Font**: `Geist` (或系统无衬线字体) - 用于中文正文和界面通用标签，确保清晰度。
- **Code/Data Font**: `JetBrains Mono` - 用于系统 ID、日志、代码块和所有数字展示。

### 字号规范 (Type Scale)
- **Headline (标题)**: 24px / Bold / Tracking -2% - 用于模块主标题。
- **Title (次级标题)**: 16px / Bold / Tracking -1% - 用于卡片或侧栏标题。
- **Body (正文)**: 14px / Regular - 用于大部分列表和会话内容。
- **Label (标签)**: 12px / Medium / All Caps - 用于按钮和状态标识。
- **Data (数据日志)**: 13px / Mono - 核心监控数据的专属字号。

---

## 组件特征 (Component Signatures)

### 边框与容器
- **Border-radius**: `0px` (严格执行)
- **Border-width**: `1px`
- **Shadows**: 仅使用 2px 或 4px 的硬投影（Hard Shadows），不使用模糊效果，模仿物理叠层感。

### 按钮 (Buttons)
- **Primary**: 背景色 Primary，文字色白色，无圆角。
- **Ghost**: 无背景，1px 边框。
- **Interaction**: 点击时触发 `Invert`（反色）效果或 `Active: scale-95`。

### 状态标签 (Status Tags)
采用 `[ STATUS ]` 格式，利用等宽字体和方括号营造字符化界面的质感。
- 正常: `[ 正常 ]` (#2E7D32)
- 警告: `[ 警告 ]` (#E65100)

---

## 栅格与间距 (Grid & Spacing)
- **Base Unit**: `4px`
- **Gutter**: `16px` / `24px`
- **Sidebar Width**: `256px` (64 units)
- **Layout**: 严格的网格对齐，所有元素必须落在 4px 的基数上，模拟表格排版的秩序感。
