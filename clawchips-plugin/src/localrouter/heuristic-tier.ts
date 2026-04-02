/**
 * Heuristic request tiering
 *
 * Weighted feature scores map to two tiers: LOCAL vs CLOUD, using a single boundary
 * (`tierBoundaries.localCloud`). Below the confidence threshold, `tier` is null (ambiguous).
 *
 * Keyword matching uses ASCII whole-token boundaries where appropriate; non-ASCII phrases
 * and paths use substring semantics.
 *
 */

import type { ScoringResult, Tier } from "./types.js";

export type ScoringConfig = {
  tokenCountThresholds: { simple: number; complex: number };
  codeKeywords: string[];
  reasoningKeywords: string[];
  simpleKeywords: string[];
  technicalKeywords: string[];
  creativeKeywords: string[];
  imperativeVerbs: string[];
  constraintIndicators: string[];
  outputFormatKeywords: string[];
  referenceKeywords: string[];
  negationKeywords: string[];
  domainSpecificKeywords: string[];
  agenticTaskKeywords: string[];
  dimensionWeights: Record<string, number>;
  /** Single split: score < localCloud → LOCAL, else CLOUD */
  tierBoundaries: {
    localCloud: number;
  };
  confidenceSteepness: number;
  confidenceThreshold: number;
};

export const DEFAULT_SCORING_CONFIG: ScoringConfig = {
  tokenCountThresholds: {
    simple: 20,
    complex: 500
  },
  codeKeywords: [
    "function", "class", "import", "def", "select", "async",
    "await", "const", "let", "var", "return", "```",
    "/workspace/", "fixtures/", "fixtures/media/image.jpg", "image.jpg", "menu.jpeg", "sqlite",
    "wal", "xss", "html", "javascript", "schema", "protocol",
    "decoder.py", "filter.py", "migrate_data.py", "decode.py", "cuda", "int8",
    "jsonl", "python", "函数", "类", "导入", "定义",
    "查询", "异步", "等待", "常量", "变量", "返回",
    "関数", "クラス", "インポート", "非同期", "定数", "変数",
    "функция", "класс", "импорт", "определ", "запрос", "асинхронный",
    "ожидать", "константа", "переменная", "вернуть", "funktion", "klasse",
    "importieren", "definieren", "abfrage", "asynchron", "erwarten", "konstante",
    "variable", "zurückgeben", "función", "clase", "importar", "definir",
    "consulta", "asíncrono", "esperar", "constante", "variable", "retornar",
    "função", "classe", "importar", "definir", "consulta", "assíncrono",
    "aguardar", "constante", "variável", "retornar", "함수", "클래스",
    "가져오기", "정의", "쿼리", "비동기", "대기", "상수",
    "변수", "반환", "دالة", "فئة", "استيراد", "تعريف",
    "استعلام", "غير متزامن", "انتظار", "ثابت", "متغير", "إرجاع",
  ],
  reasoningKeywords: [
    "prove", "theorem", "derive", "step by step", "chain of thought", "formally",
    "mathematical", "proof", "logically", "证明", "定理", "推导",
    "逐步", "思维链", "形式化", "数学", "逻辑", "証明",
    "定理", "導出", "ステップバイステップ", "論理的", "доказать", "докажи",
    "доказательств", "теорема", "вывести", "шаг за шагом", "пошагово", "поэтапно",
    "цепочка рассуждений", "рассуждени", "формально", "математически", "логически", "beweisen",
    "beweis", "theorem", "ableiten", "schritt für schritt", "gedankenkette", "formal",
    "mathematisch", "logisch", "demostrar", "teorema", "derivar", "paso a paso",
    "cadena de pensamiento", "formalmente", "matemático", "prueba", "lógicamente", "provar",
    "teorema", "derivar", "passo a passo", "cadeia de pensamento", "formalmente", "matemático",
    "prova", "logicamente", "증명", "정리", "도출", "단계별",
    "사고의 연쇄", "형식적", "수학적", "논리적", "إثبات", "نظرية",
    "اشتقاق", "خطوة بخطوة", "سلسلة التفكير", "رسمياً", "رياضي", "برهان",
    "منطقياً",
  ],
  simpleKeywords: [
    "what is", "define", "translate", "hello", "yes or no", "capital of",
    "how old", "who is", "when was", "summary_source.txt", "summary_output.txt", "email_draft.txt",
    "triage_report.md", "alpha_summary.md", "quarterly_sales.csv", "company_expenses.xlsx", "src/datautils", "pyproject.toml",
    "readme.md", "inbox/", "email_01.txt", "research/ folder", "emails/ folder", "project alpha",
    "myapp_prod", "prod-db.example.com", "api.example.com", "highest spending u.s federal", "fiscal year of 1955", "什么是",
    "定义", "翻译", "你好", "是否", "首都", "多大",
    "谁是", "何时", "项目alpha", "第二大脑", "とは", "定義",
    "翻訳", "こんにちは", "はいかいいえ", "首都", "誰", "что такое",
    "определение", "перевести", "переведи", "привет", "да или нет", "столица",
    "сколько лет", "кто такой", "когда", "объясни", "was ist", "definiere",
    "übersetze", "hallo", "ja oder nein", "hauptstadt", "wie alt", "wer ist",
    "wann", "erkläre", "qué es", "definir", "traducir", "hola",
    "sí o no", "capital de", "cuántos años", "quién es", "cuándo", "o que é",
    "definir", "traduzir", "olá", "sim ou não", "capital de", "quantos anos",
    "quem é", "quando", "무엇", "정의", "번역", "안녕하세요",
    "예 또는 아니오", "수도", "누구", "언제", "ما هو", "تعريف",
    "ترجم", "مرحبا", "نعم أو لا", "عاصمة", "من هو", "متى",
  ],
  technicalKeywords: [
    "algorithm", "optimize", "architecture", "distributed", "kubernetes", "microservice",
    "database", "infrastructure", "ocr", "pdf", "treasury bulletin", "stock price",
    "wttr.in", "icalendar", "humanizer", "skill registry", "cve", "cvss",
    "log4j", "log4shell", "jndi", "eu ai act", "regulatory", "compliance",
    "redis", "valkey", "license change", "10-k", "gaap", "basis points",
    "cagr", "gross margin", "cash requirements", "inventory turnover", "same-store sales", "merger",
    "nippon steel", "airbnb", "netflix", "us steel", "palantir", "dutch bros",
    "synopsys", "micron", "treasury", "fiscal year", "nominal dollars", "peer-reviewed",
    "conference papers", "logo", "menu", "phone", "painting", "calendar service",
    "vpn", "redmine", "merge request", "merge_requests", "arxiv", "arxiv.org",
    "163.com", "current system", "system users", "user list", "agent-browser", "agent-brower",
    "image page", "resolution", "expense report", "api integrations", "scheduled jobs", "observability",
    "apm", "conference", "conferences", "website", "project timeline", "beta release",
    "beta launch", "deadline", "frontmatter", "multi-session", "sessions field", "sequence of prompts",
    "算法", "优化", "架构", "分布式", "微服务", "数据库",
    "基础设施", "当前系统", "系统用户", "用户列表", "合并请求", "论文",
    "分辨率", "图片页面", "多轮交互", "各轮", "会话", "科技峰会",
    "科技会议", "官网", "项目时间线", "beta发布", "beta版本发布", "截止日",
    "截止日期", "frontmatter字段", "前置元数据", "会话字段", "提示顺序", "日历权限",
    "监管", "合规", "基点", "名义美元", "技能注册表", "人性化改写",
    "人性化润色", "虚拟专用网络", "文字识别", "股价", "许可证变更", "毛利率",
    "现金需求", "库存周转率", "同店销售", "并购", "同行评审", "会议论文",
    "标志", "菜单", "手机", "字画", "日历服务", "报销",
    "api集成", "定时任务", "可观测性", "アルゴリズム", "最適化", "アーキテクチャ",
    "分散", "マイクロサービス", "データベース", "алгоритм", "оптимизировать", "оптимизаци",
    "оптимизируй", "архитектура", "распределённый", "микросервис", "база данных", "инфраструктура",
    "algorithmus", "optimieren", "architektur", "verteilt", "kubernetes", "mikroservice",
    "datenbank", "infrastruktur", "algoritmo", "optimizar", "arquitectura", "distribuido",
    "microservicio", "base de datos", "infraestructura", "algoritmo", "otimizar", "arquitetura",
    "distribuído", "microsserviço", "banco de dados", "infraestrutura", "알고리즘", "최적화",
    "아키텍처", "분산", "마이크로서비스", "데이터베이스", "인프라", "خوارزمية",
    "تحسين", "بنية", "موزع", "خدمة مصغرة", "قاعدة بيانات", "بنية تحتية",
  ],
  creativeKeywords: [
    "story", "poem", "compose", "brainstorm", "creative", "imagine",
    "write a", "blog post", "long form", "image", "visual", "depict",
    "png", "博文", "撰写", "故事", "诗", "创作",
    "头脑风暴", "创意", "想象", "写一个", "图像", "图片",
    "视觉", "描绘", "png图片", "png文件", "物語", "詩",
    "作曲", "ブレインストーム", "創造的", "想像", "история", "рассказ",
    "стихотворение", "сочинить", "сочини", "мозговой штурм", "творческий", "представить",
    "придумай", "напиши", "geschichte", "gedicht", "komponieren", "brainstorming",
    "kreativ", "vorstellen", "schreibe", "erzählung", "historia", "poema",
    "componer", "lluvia de ideas", "creativo", "imaginar", "escribe", "história",
    "poema", "compor", "criativo", "imaginar", "escreva", "이야기",
    "시", "작곡", "브레인스토밍", "창의적", "상상", "작성",
    "قصة", "قصيدة", "تأليف", "عصف ذهني", "إبداعي", "تخيل",
    "اكتب",
  ],
  imperativeVerbs: [
    "build", "create", "implement", "design", "develop", "construct",
    "generate", "deploy", "configure", "set up", "schedule", "book",
    "draft", "summarize", "rewrite", "humanize", "research", "calculate",
    "inspect", "explain", "look up", "triage", "coordinate", "extract",
    "search", "identify", "构建", "创建", "实现", "设计",
    "开发", "生成", "部署", "配置", "设置", "安排",
    "预订", "起草", "总结", "改写", "润色", "调研",
    "计算", "检查", "解释", "查找", "分拣", "协调",
    "提取", "搜索", "识别", "看一下", "看下", "構築",
    "作成", "実装", "設計", "開発", "生成", "デプロイ",
    "設定", "построить", "построй", "создать", "создай", "реализовать",
    "реализуй", "спроектировать", "разработать", "разработай", "сконструировать", "сгенерировать",
    "сгенерируй", "развернуть", "разверни", "настроить", "настрой", "erstellen",
    "bauen", "implementieren", "entwerfen", "entwickeln", "konstruieren", "generieren",
    "bereitstellen", "konfigurieren", "einrichten", "construir", "crear", "implementar",
    "diseñar", "desarrollar", "generar", "desplegar", "configurar", "construir",
    "criar", "implementar", "projetar", "desenvolver", "gerar", "implantar",
    "configurar", "구축", "생성", "구현", "설계", "개발",
    "배포", "설정", "بناء", "إنشاء", "تنفيذ", "تصميم",
    "تطوير", "توليد", "نشر", "إعداد",
  ],
  constraintIndicators: [
    "under", "at most", "at least", "within", "no more than", "o(",
    "maximum", "minimum", "limit", "budget", "不超过", "至少",
    "最多", "在内", "最大", "最小", "限制", "预算",
    "以下", "最大", "最小", "制限", "予算", "не более",
    "не менее", "как минимум", "в пределах", "максимум", "минимум", "ограничение",
    "бюджет", "höchstens", "mindestens", "innerhalb", "nicht mehr als", "maximal",
    "minimal", "grenze", "budget", "como máximo", "al menos", "dentro de",
    "no más de", "máximo", "mínimo", "límite", "presupuesto", "no máximo",
    "pelo menos", "dentro de", "não mais que", "máximo", "mínimo", "limite",
    "orçamento", "이하", "이상", "최대", "최소", "제한",
    "예산", "على الأكثر", "على الأقل", "ضمن", "لا يزيد عن", "أقصى",
    "أدنى", "حد", "ميزانية",
  ],
  outputFormatKeywords: [
    "json", "yaml", "xml", "table", "csv", "markdown",
    "schema", "format as", "structured", "answer.txt", "stock_report.txt", "blog_post.md",
    "weather.py", "events.md", "humanized_blog.txt", "market_research.md", "eli5_summary.txt", "<final_answer>",
    "final_answer", "ics", "jsonl", "表格", "格式化为", "结构化",
    "markdown格式", "模式", "按...格式输出", "结构化输出", "テーブル", "フォーマット",
    "構造化", "таблица", "форматировать как", "структурированный", "tabelle", "formatieren als",
    "strukturiert", "tabla", "formatear como", "estructurado", "tabela", "formatar como",
    "estruturado", "테이블", "형식", "구조화", "جدول", "تنسيق",
    "منظم",
  ],
  referenceKeywords: [
    "above", "below", "previous", "following", "the docs", "the api",
    "the code", "earlier", "attached", "attached image", "image path", "container files",
    "file:", "scanned", "official status page", "community discussions", "status page", "as of ",
    "latest", "put your answer between", "http://", "https://", "www.", "url",
    "link", "one answer per line", "notes.md", "config.json", "openclaw_report.pdf", "gpt4.pdf",
    "上面", "下面", "之前", "接下来", "文档", "代码",
    "附件", "链接", "网址", "附图", "文件路径", "图片路径",
    "镜像路径", "容器文件", "扫描件", "扫描版", "官方状态页", "社区讨论",
    "状态页", "截至", "最新", "前文", "后文", "参考上文",
    "参考下文", "逐行回答", "上記", "下記", "前の", "次の",
    "ドキュメント", "コード", "выше", "ниже", "предыдущий", "следующий",
    "документация", "код", "ранее", "вложение", "oben", "unten",
    "vorherige", "folgende", "dokumentation", "der code", "früher", "anhang",
    "arriba", "abajo", "anterior", "siguiente", "documentación", "el código",
    "adjunto", "acima", "abaixo", "anterior", "seguinte", "documentação",
    "o código", "anexo", "위", "아래", "이전", "다음",
    "문서", "코드", "첨부", "أعلاه", "أدناه", "السابق",
    "التالي", "الوثائق", "الكود", "مرفق",
  ],
  negationKeywords: [
    "don't", "do not", "avoid", "never", "without", "except",
    "exclude", "no longer", "不要", "避免", "从不", "没有",
    "除了", "排除", "しないで", "避ける", "決して", "なしで",
    "除く", "не делай", "не надо", "нельзя", "избегать", "никогда",
    "без", "кроме", "исключить", "больше не", "nicht", "vermeide",
    "niemals", "ohne", "außer", "ausschließen", "nicht mehr", "no hagas",
    "evitar", "nunca", "sin", "excepto", "excluir", "não faça",
    "evitar", "nunca", "sem", "exceto", "excluir", "하지 마",
    "피하다", "절대", "없이", "제외", "لا تفعل", "تجنب",
    "أبداً", "بدون", "باستثناء", "استبعاد",
  ],
  domainSpecificKeywords: [
    "quantum", "fpga", "vlsi", "risc-v", "asic", "photonics",
    "genomics", "proteomics", "topological", "homomorphic", "zero-knowledge", "lattice-based",
    "airbnb", "netflix", "palantir", "dutch bros", "synopsys", "micron",
    "us steel", "tko", "endeavor", "payment gateway", "pci dss", "cisa",
    "federal government", "treasury bulletin", "orin", "llama-7b", "video-mme", "observability",
    "apm", "量子", "光子学", "基因组学", "蛋白质组学", "拓扑",
    "同态", "零知识", "格密码", "支付网关", "联邦政府", "国库公报",
    "可观测性", "拓扑学", "同态加密", "零知识证明", "格基", "量子",
    "フォトニクス", "ゲノミクス", "トポロジカル", "квантовый", "фотоника", "геномика",
    "протеомика", "топологический", "гомоморфный", "с нулевым разглашением", "на основе решёток", "quanten",
    "photonik", "genomik", "proteomik", "topologisch", "homomorph", "zero-knowledge",
    "gitterbasiert", "cuántico", "fotónica", "genómica", "proteómica", "topológico",
    "homomórfico", "quântico", "fotônica", "genômica", "proteômica", "topológico",
    "homomórfico", "양자", "포토닉스", "유전체학", "위상", "동형",
    "كمي", "ضوئيات", "جينوميات", "طوبولوجي", "تماثلي",
  ],
  agenticTaskKeywords: [
    "read file", "read the file", "look at", "check the", "open the", "edit",
    "modify", "update the", "change the", "write to", "create file", "execute",
    "deploy", "install", "npm", "pip", "compile", "after that",
    "and also", "once done", "step 1", "step 2", "fix", "debug",
    "until it works", "keep trying", "iterate", "make sure", "verify", "confirm",
    "read the project notes", "save your answer", "write answer to", "frontmatter", "sessions field", "sequence of prompts",
    "读取文件", "读取这个文件", "查看", "检查", "打开", "编辑",
    "修改", "更新", "更改", "写入", "创建", "创建文件",
    "执行", "部署", "安装", "编译", "之后", "并且",
    "完成后", "第一步", "第二步", "修复", "调试", "直到",
    "继续尝试", "迭代", "确保", "确认", "验证", "读取项目笔记",
    "阅读项目笔记", "保存答案", "将答案写入", "写入答案", "frontmatter字段", "会话字段",
    "提示顺序", "leer archivo", "editar", "modificar", "actualizar", "ejecutar",
    "desplegar", "instalar", "paso 1", "paso 2", "arreglar", "depurar",
    "verificar", "ler arquivo", "editar", "modificar", "atualizar", "executar",
    "implantar", "instalar", "passo 1", "passo 2", "corrigir", "depurar",
    "verificar", "파일 읽기", "편집", "수정", "업데이트", "실행",
    "배포", "설치", "단계 1", "단계 2", "디버그", "확인",
    "قراءة ملف", "تحرير", "تعديل", "تحديث", "تنفيذ", "نشر",
    "تثبيت", "الخطوة 1", "الخطوة 2", "إصلاح", "تصحيح", "تحقق",
  ],
  dimensionWeights: {
    agenticTask: 0.039453,
    codePresence: 0.159529,
    constraintCount: 0.022797,
    creativeMarkers: 0.105751,
    domainSpecificity: 0.114365,
    imperativeVerbs: 0.050579,
    multiStepPatterns: 0.105987,
    negationComplexity: 0.006742,
    outputFormat: 0.062046,
    questionComplexity: 0.0581,
    reasoningMarkers: 0.09026,
    referenceComplexity: 0.023641,
    simpleIndicators: 0.031903,
    technicalTerms: 0.167652,
    tokenCount: 0.013185,
  },
  tierBoundaries: {
    localCloud: 0.08
  },
  confidenceSteepness: 12,
  confidenceThreshold: 0.5,
};

/** One scored dimension: name, score in [-1, 1] (or tier-specific), optional human-readable signal. */
type Slice = { name: string; score: number; signal: string | null };

/** Maps a config keyword list to a dimension name, UI tag, hit thresholds, and tri-state scores. */
type KeywordGroup = {
  readonly field: keyof Pick<
    ScoringConfig,
    | "codeKeywords"
    | "reasoningKeywords"
    | "technicalKeywords"
    | "creativeKeywords"
    | "simpleKeywords"
    | "imperativeVerbs"
    | "constraintIndicators"
    | "outputFormatKeywords"
    | "referenceKeywords"
    | "negationKeywords"
    | "domainSpecificKeywords"
  >;
  readonly dimension: string;
  readonly tag: string;
  readonly gate: { low: number; high: number };
  readonly out: { none: number; low: number; high: number };
};

/** Ordered keyword groups: gate = min hits for low/high score bands; technical uses high=3. */
const KEYWORD_GROUPS: readonly KeywordGroup[] = [
  {
    field: "codeKeywords",
    dimension: "codePresence",
    tag: "code",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.5, high: 1.0 },
  },
  {
    field: "reasoningKeywords",
    dimension: "reasoningMarkers",
    tag: "reasoning",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.7, high: 1.0 },
  },
  {
    field: "technicalKeywords",
    dimension: "technicalTerms",
    tag: "technical",
    gate: { low: 1, high: 3 },
    out: { none: 0, low: 0.5, high: 1.0 },
  },
  {
    field: "creativeKeywords",
    dimension: "creativeMarkers",
    tag: "creative",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.5, high: 0.7 },
  },
  {
    field: "simpleKeywords",
    dimension: "simpleIndicators",
    tag: "simple",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: -1.0, high: -1.0 },
  },
  {
    field: "imperativeVerbs",
    dimension: "imperativeVerbs",
    tag: "imperative",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.3, high: 0.5 },
  },
  {
    field: "constraintIndicators",
    dimension: "constraintCount",
    tag: "constraints",
    gate: { low: 1, high: 3 },
    out: { none: 0, low: 0.3, high: 0.7 },
  },
  {
    field: "outputFormatKeywords",
    dimension: "outputFormat",
    tag: "format",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.4, high: 0.7 },
  },
  {
    field: "referenceKeywords",
    dimension: "referenceComplexity",
    tag: "references",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.3, high: 0.5 },
  },
  {
    field: "negationKeywords",
    dimension: "negationComplexity",
    tag: "negation",
    gate: { low: 2, high: 3 },
    out: { none: 0, low: 0.3, high: 0.5 },
  },
  {
    field: "domainSpecificKeywords",
    dimension: "domainSpecificity",
    tag: "domain-specific",
    gate: { low: 1, high: 2 },
    out: { none: 0, low: 0.5, high: 0.8 },
  },
];

/** Escapes characters that are special in `RegExp` when embedding user/keyword text literally. */
function reEscape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Lazily compiled ASCII boundary regexes keyed by stable string (avoids rebuilding hundreds of
 * patterns on every `scoreHeuristic` call for fixed keyword lists).
 */
const keywordBoundaryRegexpCache = new Map<string, RegExp>();

function cachedBoundaryRegexp(cacheKey: string, build: () => RegExp): RegExp {
  let re = keywordBoundaryRegexpCache.get(cacheKey);
  if (re !== undefined) return re;
  re = build();
  keywordBoundaryRegexpCache.set(cacheKey, re);
  return re;
}

/**
 * Returns whether `keyword` matches `text` (callers pass lowercased `text`).
 * ASCII tokens/phrases use word-boundary rules; non-ASCII and symbols use substring match.
 */
function keywordMatchesText(text: string, keyword: string): boolean {
  const kw = (keyword ?? "").trim();
  if (!kw) return false;
  // Non-ASCII: substring match (text is already lowercased in callers).
  if (!/^[\x00-\x7f]+$/.test(kw)) {
    return text.includes(kw.toLowerCase());
  }
  // ASCII: branch on spaced phrases vs single token vs symbol-like tokens.
  if (kw.includes(" ")) {
    const parts = kw.split(/\s+/).filter((p) => p.length > 0);
    if (parts.length === 0) return false;
    if (parts.every((p) => /^[A-Za-z0-9_]+$/.test(p))) {
      const key = `sp:${kw.toLowerCase()}`;
      const pattern = cachedBoundaryRegexp(key, () =>
        new RegExp(
          `(?<![A-Za-z0-9])${parts.map((p) => reEscape(p)).join("\\s+")}(?![A-Za-z0-9])`,
          "i",
        ),
      );
      return pattern.test(text);
    }
    return text.includes(kw.toLowerCase());
  }
  if (/^[A-Za-z0-9_]+$/.test(kw)) {
    const key = `w:${kw.toLowerCase()}`;
    const pattern = cachedBoundaryRegexp(key, () => new RegExp(`(?<![A-Za-z0-9])${reEscape(kw)}(?![A-Za-z0-9])`, "i"));
    return pattern.test(text);
  }
  // Other ASCII (e.g. backticks, slashes): substring match.
  return text.includes(kw.toLowerCase());
}

/** Scores one keyword group from `cfg` against `text` using hit counts vs `group.gate` → `group.out`. */
function groupSlice(text: string, cfg: ScoringConfig, group: KeywordGroup): Slice {
  const matches = cfg[group.field].filter((k) => keywordMatchesText(text, k));
  const hits = matches.length;
  const { low, high } = group.gate;
  const { none, low: sLow, high: sHigh } = group.out;
  if (hits >= high) {
    const sample = matches.slice(0, 3);
    return {
      name: group.dimension,
      score: sHigh,
      signal: `${group.tag} (${sample.join(", ")})`,
    };
  }
  if (hits >= low) {
    const sample = matches.slice(0, 3);
    return {
      name: group.dimension,
      score: sLow,
      signal: `${group.tag} (${sample.join(", ")})`,
    };
  }
  return { name: group.dimension, score: none, signal: null };
}

/** Maps estimated token count to a token-length dimension score (short / neutral / long). */
function spanFromTokenEstimate(
  estimatedTokens: number,
  t: ScoringConfig["tokenCountThresholds"],
): Slice {
  if (estimatedTokens < t.simple) {
    return {
      name: "tokenCount",
      score: -1.0,
      signal: `short (${estimatedTokens} tokens)`,
    };
  }
  if (estimatedTokens > t.complex) {
    return {
      name: "tokenCount",
      score: 1.0,
      signal: `long (${estimatedTokens} tokens)`,
    };
  }
  return { name: "tokenCount", score: 0, signal: null };
}

/** Detects multi-step or procedural phrasing in lowercased text via fixed regex cues. */
function proceduralCueSlice(lower: string): Slice {
  const cues = [/first.*then/i, /step \d/i, /\d\.\s/];
  const any = cues.some((re) => re.test(lower));
  return {
    name: "multiStepPatterns",
    score: any ? 0.5 : 0,
    signal: any ? "multi-step" : null,
  };
}

/** Adds a small score when the original prompt contains many question marks (heavy Q&A load). */
function manyQuestionsSlice(originalPrompt: string): Slice {
  const q = (originalPrompt.match(/\?/g) || []).length;
  if (q > 3) {
    return { name: "questionComplexity", score: 0.5, signal: `${q} questions` };
  }
  return { name: "questionComplexity", score: 0, signal: null };
}

/**
 * Scores agentic-tooling cues from `phrases` against lowercased text and returns both a slice and a 0–1 agentic score.
 */
function agenticBundle(
  lower: string,
  phrases: string[],
): { slice: Slice; agenticScore: number } {
  const matched: string[] = [];
  for (const w of phrases) {
    if (keywordMatchesText(lower, w)) {
      matched.push(w);
    }
  }
  const k = matched.length;
  const preview = matched.slice(0, 3);
  if (k >= 4) {
    return {
      slice: {
        name: "agenticTask",
        score: 1.0,
        signal: `agentic (${preview.join(", ")})`,
      },
      agenticScore: 1.0,
    };
  }
  if (k >= 3) {
    return {
      slice: {
        name: "agenticTask",
        score: 0.6,
        signal: `agentic (${preview.join(", ")})`,
      },
      agenticScore: 0.6,
    };
  }
  if (k >= 1) {
    return {
      slice: {
        name: "agenticTask",
        score: 0.2,
        signal: `agentic-light (${preview.join(", ")})`,
      },
      agenticScore: 0.2,
    };
  }
  return {
    slice: { name: "agenticTask", score: 0, signal: null },
    agenticScore: 0,
  };
}

/** Weighted sum of slice scores using `weights` keyed by dimension name. */
function weightedBlend(slices: Slice[], weights: Record<string, number>): number {
  let sum = 0;
  for (const s of slices) {
    sum += s.score * (weights[s.name] ?? 0);
  }
  return sum;
}

/** Sigmoid-style confidence from distance to the LOCAL/CLOUD boundary (`steepness` controls slope). */
function logisticFromDistance(distance: number, steepness: number): number {
  return 1 / (1 + Math.exp(-steepness * distance));
}

/**
 * Chooses LOCAL vs CLOUD from the blended score and returns the distance to the split point
 * (used for confidence).
 */
function pickLocalCloudTier(
  blended: number,
  localCloud: number,
): { tier: Tier; distance: number } {
  if (blended < localCloud) {
    return { tier: "LOCAL", distance: localCloud - blended };
  }
  return { tier: "CLOUD", distance: blended - localCloud };
}

/**
 * Classifies a user prompt into LOCAL vs CLOUD (or ambiguous) using weighted heuristic dimensions.
 * @param prompt User message text.
 * @param _systemPrompt Reserved for future use (e.g. joint token estimate); scoring uses `prompt` for keyword match.
 * @param estimatedTokens Precomputed token estimate for the request (length signal).
 * @param config Optional scoring config; defaults to `DEFAULT_SCORING_CONFIG` when omitted.
 */
export function classifyByHeuristicTier(
  prompt: string,
  _systemPrompt: string | undefined,
  estimatedTokens: number,
  config?: ScoringConfig,
): ScoringResult {
  // Resolve config (caller override or bundled default).
  const cfg = config ?? DEFAULT_SCORING_CONFIG;

  // Normalize prompt for keyword matching (ASCII rules assume lowercased text).
  const userLower = prompt.toLowerCase();

  // Build all dimension slices — token length, each keyword group, multi-step cues, question load, agentic cues.
  const slices: Slice[] = [spanFromTokenEstimate(estimatedTokens, cfg.tokenCountThresholds)];
  for (const group of KEYWORD_GROUPS) {
    slices.push(groupSlice(userLower, cfg, group));
  }
  slices.push(proceduralCueSlice(userLower));
  slices.push(manyQuestionsSlice(prompt));

  const agent = agenticBundle(userLower, cfg.agenticTaskKeywords);
  slices.push(agent.slice);
  const agenticScore = agent.agenticScore;

  // Collect human-readable signals from slices that fired.
  const signals = slices.filter((s) => s.signal !== null).map((s) => s.signal!);

  // Single scalar score = weighted sum of dimension scores.
  const blended = weightedBlend(slices, cfg.dimensionWeights);

  // Compare blended score to `localCloud` and measure distance to that boundary.
  const localCloud = cfg.tierBoundaries.localCloud;
  const { tier: rawTier, distance } = pickLocalCloudTier(blended, localCloud);

  // Turn boundary distance into a confidence in (0, 1) via logistic curve.
  const confidence = logisticFromDistance(distance, cfg.confidenceSteepness);

  // If confidence is too low, mark tier ambiguous (`null`); otherwise keep LOCAL or CLOUD.
  if (confidence < cfg.confidenceThreshold) {
    return {
      score: blended,
      tier: null,
      confidence,
      signals,
      agenticScore,
    };
  }

  return {
    score: blended,
    tier: rawTier,
    confidence,
    signals,
    agenticScore,
  };
}
