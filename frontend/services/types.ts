/**
 * API 契约类型 —— 以后端 serializers 实际字段为准（2026-10-15 冻结，后端已通过 check）。
 * Wave 1 各模块 agent 只读此文件，不得自行杜撰字段；发现缺口先在群里登记，由主会话补充。
 */

// ============ 通用 ============
/** 分页响应（DefaultPagination：page=1, page_size=20, max 100） */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// ============ accounts（登录/公司/员工/岗位） ============
export interface User {
  id: number;
  phone: string | null;
  username: string;
  real_name: string;
  avatar: string;
  gender: number; // 0 未知 1 男 2 女
  birthday: string | null;
}

export interface TokenPair {
  access: string;
  refresh: string;
  user: User;
}

export type MemberStatus = 0 | 1 | 2; // 0 待审核 1 在职 2 离职

/** 我在某公司的岗位摘要（CompanyItem.post） */
export interface CompanyPostSummary {
  id: number;
  name: string;
  is_admin: boolean;
  /** 权限点体系二期：'*' 岗位返回 ['*']，普通岗位返回解析后的全部细粒度点（后端并行任务加，可能缺省） */
  effective_perms?: string[];
}

/** GET /companies/mine 的列表项（含我在该公司的状态/岗位） */
export interface CompanyItem {
  id: number;
  name: string;
  city: string;
  company_no: string;
  /** 版本套餐：trial 体验 / basic 基础 / standard 标准 / flagship 旗舰 */
  plan: string;
  plan_display: string;
  capacity: number;
  status: MemberStatus | null;
  status_display: string;
  /** 后端返回岗位对象或 null（勿当字符串用） */
  post: CompanyPostSummary | null;
  member_count: number;
  created_at: string;
}

export interface Post {
  id: number;
  name: string;
  permissions: string[];
  /** 权限点体系二期：解析后的全部细粒度点（'*' 岗位返回 ['*']；后端并行任务加，可能缺省） */
  effective_perms?: string[];
  is_admin: boolean;
  is_system: boolean;
  member_count: number;
}

/** 权限点字典（GET /permissions，Plan A）：legacy=旧 coarse key 覆盖的点 */
export interface PermissionPoint {
  key: string;
  label: string;
  legacy: boolean;
  legacy_key?: string;
}

export interface PermissionGroup {
  module: string;
  label: string;
  points: PermissionPoint[];
}

/** GET /employees 的列表项 */
export interface Employee {
  id: number; // UserCompany 主键
  user_id: number;
  name: string;
  phone: string;
  avatar: string;
  gender: number; // 0 未知 1 男 2 女
  post_id: number | null;
  post_name: string;
  is_admin: boolean;
  status: MemberStatus;
  status_display: string;
  fixed_salary: string | null;
  /** 固定工资计算方式（'day'×出勤天数 / 'month'×月数；清空固定工资后仍返回该字段） */
  fixed_type?: 'day' | 'month';
  /** 工资计算方式（四选一互斥）：fixed 固定 / whole 整件计件 / process 工序计件 / commission 计件提成 */
  salary_type?: 'fixed' | 'whole' | 'process' | 'commission';
  salary_type_display?: string;
  /** 整件单价（salary_type=whole 时生效） */
  piece_rate?: string | null;
  /** 计件提成比例%（salary_type=commission 时生效） */
  commission_rate?: string | null;
  is_pinned: boolean;
  hired_date: string | null;
  group_no: number; // 组编号（1组/2组…，非空）
}

// ============ catalog（客户/工序/款式/菲票） ============
export interface CustomerGroup {
  id: number;
  name: string;
}

export interface Customer {
  id: number;
  customer_no: string;
  group: number | null;
  group_name?: string;
  name: string;
  phone: string;
  remark: string;
}

export interface Process {
  id: number;
  name: string;
  /** 默认单价（后端字段 default_price） */
  default_price: string;
  /** 兼容别名，勿用于新代码 */
  price_default?: string;
  is_fixed: boolean;
  sort: number;
}

/** GET /records/process-selector：扫菲时可选工序 = 固定工序(开) ∪ 指定工序。
 *  registered_qty/cap：满扎徽标（全员累计/扎数），SA3 后端新增字段，缺失=后端未升级 */
export interface ProcessSelectorItem {
  id: number;
  name: string;
  price: string;
  is_fixed: boolean;
  /** 该工序同扎全员累计登记件数（缺省/undefined=后端未升级，前端隐藏徽标） */
  registered_qty?: number | null;
  /** 扎数上限（null=床次未配总扎数，前端只显示累计数） */
  cap?: number | null;
}

export interface Style {
  id: number;
  style_no: string;
  name: string;
  price: string; // 款式总单价（系统设置可隐藏）
  customer: number | null;
  customer_name?: string;
  image: string;
  /** 款式图片多图（≤9 张相对路径数组；image 恒=首张，后端同步） */
  images?: string[];
  remark: string;
  /** 工艺说明（文字，F1/B1 契约，可能缺省） */
  craft_note?: string;
  /** 工艺图片（相对路径数组，展示用 mediaUrl 补域名） */
  craft_images?: string[];
  /** 产品视频 url（可空，展示用 mediaUrl 补域名） */
  video?: string;
  /** 面料（选填文本；建床时作默认面料） */
  fabric?: string;
  /** 详情接口自带工序配置 */
  processes?: StyleProcessItem[];
  // 统计行（后端聚合填充）
  ticket_count?: number;
  process_count?: number;
  cut_total?: number;
  done_ticket_count?: number;
  unfinished_ticket_count?: number;
}

export interface StyleProcessItem {
  id: number;
  process: number;
  process_name: string;
  price: string;
  sort?: number;
  /** 跳序开关：false 时须先完成 sort 更小的工序才能登记本工序 */
  allow_skip?: boolean;
}

/** 菲票 */
export interface Ticket {
  id: number;
  production: number;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  qty: number;
  fabric: string;
  piece_no: string;
  qr_code: string;
  is_printed: boolean;
  print_count?: number;
  print_at?: string | null;
  style_no?: string;
  style_name?: string;
}

/** 扎级完成矩阵行（GET /records/bundle-progress，2026-10-22 完成矩阵需求） */
export interface BundleProgressItem {
  ticket_id: number;
  production_id: number;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  qty: number;
  registered_qty: number;
  registered_process_count: number;
  process_count: number;
  state: 'none' | 'partial' | 'done';
  state_display: string;
  /** 完工 = distinct 已登记工序数 ≥ 配置工序数（后端 stats.py 唯一口径） */
  done: boolean;
  /** 该扎有登记的员工（累计件数降序），完工扎据此显示「谁做的」 */
  users: Array<{ id: number; name: string; qty: number }>;
}

// ============ production（床次订单） ============
export interface Production {
  id: number;
  style: number;
  style_no?: string;
  style_name?: string;
  bed_no: string;
  total_qty: number;
  /** 计划裁货数量（建床录入，与实裁分账） */
  plan_qty?: number | null;
  order_type: string;
  customer: number | null;
  customer_name?: string;
  fabric: string;
  /** 来源单号/客户订单号（质量追溯 P0 手填锚点） */
  source_order_no?: string;
  is_locked: boolean;
  is_cut_done: boolean;
  order_processes?: ProductionProcessItem[];
  // 统计行（后端聚合填充）
  ticket_count?: number;
  cut_total?: number;
  registered_qty?: number;
  done_ticket_count?: number;
  created_at?: string;
}

export interface ProductionProcessItem {
  id: number;
  process: number;
  process_name: string;
  price: string;
}

// ============ records（扫菲登记） ============
export interface RecordItem {
  id: number;
  ticket: number;
  bed_no?: string;
  bundle_no?: string;
  color?: string;
  size?: string;
  style_no?: string;
  style_name?: string;
  process: number;
  process_name?: string;
  user: number;
  user_name?: string;
  operator: number | null;
  operator_name?: string;
  qty: number;
  price: string;
  /** qty*price 保留两位（后端聚合） */
  amount?: string;
  registered_at: string;
  settlement: number | null;
}

/** POST /scan/parse 的响应（后端实际结构） */
export interface ScanParseResult {
  ticket: {
    id: number;
    production: number;
    bed_no: string;
    bundle_no: string;
    color: string;
    size: string;
    qty: number;
    qr_code: string;
  };
  style: { id: number; style_no: string; name: string };
  production: {
    id: number;
    is_locked: boolean;
    is_cut_done: boolean;
    process_count: number;
    registered_process_count: number;
    registered_qty: number;
  };
}

/** GET /records/workbench：扫菲工作台（进行中床次进度 + 本人今日登记） */
export interface Workbench {
  beds: Array<{
    id: number;
    bed_no: string;
    style_no: string;
    style_name: string;
    total_qty: number;
    registered_qty: number;
    done_ticket_count: number;
  }>;
  my_today: RecordItem[];
}

// ============ salary（薪酬结算） ============
export interface SalaryCompanySummary {
  period: { date_from: string | null; date_to: string | null };
  settled: string;
  unsettled: {
    qty: number;
    amount: string;
    users: number;
    tickets: number;
    processes: number;
    no_price_processes: number;
  };
  fixed_total: string;
}

export interface EmployeeSalaryDetail {
  period: { date_from: string | null; date_to: string | null };
  by_date: Array<{ date: string; qty: number; amount: string }>;
  by_bed: Array<{ production_id: number; bed_no: string; style_no: string; style_name: string; qty: number; amount: string }>;
  by_process: Array<{ process_id: number; name: string; qty: number; amount: string }>;
  summary: { qty: number; ticket_count: number; amount: string };
  fixed?: {
    fixed_salary: string;
    fixed_type: 'day' | 'month';
    fixed_type_display: string;
    worked_days: number;
    months: number;
    amount: string;
  };
}

export type SettlementStatus = 'settled' | 'revoked';

export interface Settlement {
  id: number;
  company: number;
  user: number | null; // null = 全员结算
  user_name: string;
  period_start: string;
  period_end: string;
  employee_count: number;
  amount: string;
  status: SettlementStatus;
  status_display: string;
  created_by: number | null;
  created_by_name: string;
  created_at: string;
}

export interface SalaryAdjust {
  id: number;
  company: number;
  user: number;
  user_name: string;
  amount: string; // 正数增加 / 负数扣减
  reason: string;
  operator: number | null;
  operator_name: string;
  created_at: string;
}

export type ApplyStatus = 0 | 1 | 2 | 3 | 4; // 0 待审核 1 待发放 2 已发放 3 已拒绝 4 已作废

export interface SettleApply {
  id: number;
  company: number;
  applicant: number;
  applicant_name: string;
  user: number;
  user_name: string;
  period_start: string;
  period_end: string;
  amount: string;
  status: ApplyStatus;
  status_display: string;
  /** 待谁审批：待审核=「待 张三 审批」；待发放=「已审核，待 X 发放」；终态空 */
  reviewer_hint: string;
  note: string;
  reviewer: number | null;
  reviewer_name: string;
  reviewed_at: string | null;
  reject_reason: string;
  paid_by: number | null;
  paid_by_name: string;
  paid_at: string | null;
  payer: string;
  void_reason: string;
  created_at: string;
}

/** GET /salary/summary/completion 三层钻取 */
export interface CompletionSummary {
  period: { date_from: string | null; date_to: string | null };
  styles: Array<{
    style_id: number;
    style_no: string;
    style_name: string;
    qty: number;
    avg_price: string;
    amount: string;
    ticket_count: number;
    process_count: number;
  }>;
  processes: Array<{
    process_id: number;
    name: string;
    qty: number;
    avg_price: string;
    amount: string;
    ticket_count: number;
    users: number;
  }>;
  matrix: Array<{ color: string; sizes: Array<{ size: string; qty: number }> }>;
  employees: Array<{ user_id: number; user_name: string; qty: number; amount: string }>;
  tickets?: Array<{
    ticket_id: number;
    bed_no: string;
    bundle_no: string;
    color: string;
    size: string;
    qty: number;
    price: string;
    amount: string;
  }>;
}

// ============ ops（通知/日志/设置） ============
export interface Notification {
  id: number;
  code: string;
  title: string;
  message: string;
  /** 0 未读 1 已读（后端为整型） */
  is_read: 0 | 1;
  created_at: string;
}

export interface AuditLog {
  id: number;
  type: string; // price/assist/qty/process/revoke/other
  type_display?: string;
  operator_name?: string;
  target_user_name?: string;
  bed_no: string;
  style_no: string;
  bundle_no: string;
  before: string | null;
  after: string | null;
  message: string;
  created_at: string;
}

/** GET /client-logs：客户端上报日志（厂→用户隔离，后端只回本公司） */
export interface ClientLogItem {
  id: number;
  company: number | null;
  user: number | null;
  user_name: string;
  user_phone: string;
  level: 'info' | 'warn' | 'error';
  page: string;
  message: string;
  detail: string;
  created_at: string;
}

/** GET/PUT /settings：KV（13 项开关见规格 §15，值均为字符串） */
export type CompanySettings = Record<string, string>;

// ============ orders（订单管理，P1 新增；契约=docs/系统重设计实施方案.md §四，SA1 并行开发中） ============
export type OrderStatus = 'producing' | 'done' | 'cancelled';

/** 订单颜色×尺码数量行（同单同色码唯一，后端校验） */
export interface OrderItem {
  id?: number;
  color: string;
  size: string;
  qty: number;
}

export interface Order {
  id: number;
  order_no: string;
  customer: number | null;
  customer_name?: string;
  /** 交期 YYYY-MM-DD（可空，有才有交期灯） */
  delivery_date: string | null;
  order_type: string; // 自产/外发
  status: OrderStatus;
  status_display?: string;
  remark: string;
  /** 以下统计字段由列表/详情接口聚合下发（后端调 stats.py 唯一口径；可能缺省，前端自算兜底） */
  total_qty?: number;
  registered_qty?: number;
  /** 完成率 0~100 */
  progress?: number;
  /** 距交期天数（过期为负；无交期为 null） */
  days_left?: number | null;
  /** 详情接口自带明细（列表可缺省） */
  items?: OrderItem[];
  created_at?: string;
  /** 以下为 F1/B1 契约新增字段（列表与详情都下发，可能缺省，前端全部兜底） */
  /** 关联款式 id */
  style?: number | null;
  style_no?: string;
  style_name?: string;
  /** 款式图（相对路径，mediaUrl 补域名） */
  style_image?: string;
  /** 备注图片（相对路径数组，mediaUrl 补域名展示） */
  images?: string[];
  /** 关联工序 [{process_id, price}]（process_name 缺省时前端用 /processes 映射兜底） */
  process_items?: Array<{ process_id: number; price: string }>;
}

// ============ 工序模板（F1/B1 契约：GET/POST /process-templates，GET/PUT/DELETE /:id） ============
export interface ProcessTemplateItem {
  process_id: number;
  price: string;
  /** 后端可能缺省：缺省时前端用 /processes 列表自行映射兜底 */
  process_name?: string;
}

export interface ProcessTemplate {
  id: number;
  name: string;
  items: ProcessTemplateItem[];
}

/** GET /orders/<id>/progress 单格：分母=OrderItem.qty，分子=挂单床次菲票登记按色码聚合 */
export interface OrderProgressCell {
  color: string;
  size: string;
  order_qty: number;
  registered_qty: number;
  /** 已裁（挂单床次菲票按色码聚合）——开床余量口径（2026-10-22 增补） */
  cut_qty?: number;
  /** 0~100 */
  progress: number;
}

/** GET /orders/<id>/progress：色码矩阵完成度（后端内部调 stats.py，两处零分叉） */
export interface OrderProgress {
  cells: OrderProgressCell[];
  /** SA1 实际返回字段（与 cells 同构，qty 对应 order_qty） */
  items?: Array<{ id?: number; color: string; size: string; qty: number; registered_qty: number; cut_qty?: number; progress: number; lagging?: boolean }>;
  total: { order_qty: number; registered_qty: number; progress: number };
  /** 全单已裁合计（2026-10-22 增补） */
  cut_qty?: number;
}
