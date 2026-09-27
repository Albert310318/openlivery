export type User = {
  id: string;
  name: string;
  email: string;
  role: string;
  is_vendiq_admin: boolean;
  agency: Agency;
  restaurant_role?: "admin" | "cashier" | "waiter" | "kitchen" | "delivery" | null;
  restaurant_client_id?: string | null;
  restaurant_client_name?: string | null;
};

export type RestaurantOrderItem = {
  id: string;
  product_id: string;
  product_name: string;
  base_unit_price: string;
  unit_price: string;
  quantity: number;
  line_subtotal: string;
  variants: { id: string; name: string; price_delta?: string; price?: string }[];
  extras: { id: string; name: string; price_delta?: string; price?: string }[];
  observations: string | null;
  is_cancelled: boolean;
  cancelled_at: string | null;
};
export type RestaurantOrder = {
  id: string;
  client_id: string;
  order_number: string;
  source: "whatsapp" | "waiter";
  modality: "dine_in" | "pickup" | "delivery";
  table_account_id: string | null;
  table_number: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  address: string | null;
  address_reference: string | null;
  delivery_zone_name: string | null;
  subtotal: string;
  delivery_fee: string;
  total: string;
  order_status: string;
  payment_status: string;
  payment_verification_status: string;
  payment_method: string | null;
  receipt_submitted: boolean;
  payment_reference: string | null;
  reported_payment_amount: string | null;
  payment_reported_at: string | null;
  payment_review_status: string;
  payment_rejection_reason: string | null;
  payment_rejected_at: string | null;
  payment_receipt_available: boolean;
  payment_receipt_filename: string | null;
  payment_receipt_mime: string | null;
  notes: string | null;
  created_by_user_id: string | null;
  conversation_id: string | null;
  created_at: string;
  updated_at: string;
  payment_confirmed_at: string | null;
  ready_at: string | null;
  delivered_at: string | null;
  items: RestaurantOrderItem[];
};
export type DeliveryNotification = { id: string; client_id: string; order_id: string; order_number: string; recipient: string; provider: string | null; status: "pending" | "sent" | "error"; external_message_id: string | null; error_message: string | null; attempts: number; sent_at: string | null; updated_at: string };
export type DeliveryOrder = {
  id: string;
  client_id: string;
  order_number: string;
  modality: "delivery";
  customer_name: string | null;
  customer_phone: string | null;
  address: string | null;
  address_reference: string | null;
  order_status: string;
  items: { id: string; product_name: string; quantity: number; observations: string | null; variants: { name: string }[]; extras: { name: string }[] }[];
  created_at: string;
  ready_at: string | null;
  delivered_at: string | null;
};
export type RestaurantTableAccount = {
  id: string;
  client_id: string;
  table_number: string;
  status: string;
  is_open: boolean;
  opened_at: string;
  paid_at: string | null;
  closed_at: string | null;
  subtotal: string;
  total: string;
  orders: RestaurantOrder[];
};
export type RestaurantMenuOption = { id: string; name: string; price: string; price_delta?: string; is_available: boolean };
export type RestaurantMenuProduct = { id: string; name: string; price: string; is_available: boolean; variants: RestaurantMenuOption[]; extras: RestaurantMenuOption[] };
export type RestaurantMenuCategory = { id: string; name: string; products: RestaurantMenuProduct[] };
export type RestaurantStaffAssignment = { user_id: string; role: string; is_active: boolean; user: { id: string; name: string; email: string; phone: string | null } };

export type Agency = { id: string; name: string; slug: string; brand_color: string; logo_url: string | null };

export type AgentSummary = { id: string; name: string; description: string; is_active: boolean };

export type Client = {
  id: string;
  name: string;
  industry: string;
  description: string;
  general_context: string;
  is_active: boolean;
  portal_slug: string;
  portal_enabled: boolean;
  portal_title: string;
  portal_email: string | null;
  portal_email_verified_at: string | null;
  portal_password_configured: boolean;
  portal_domain: string | null;
  portal_domain_verified: boolean;
  sales_advisor_phone: string | null;
  agents: AgentSummary[];
  created_at: string;
  updated_at: string;
};

export type ClientDomain = {
  domain: string | null;
  verified: boolean;
  txt_host: string | null;
  txt_value: string | null;
};

export type Agent = {
  id: string;
  client_id: string;
  provider: string;
  name: string;
  description: string;
  instructions: string;
  personality: string;
  brief_summary: string;
  brief_products: string;
  brief_audience: string;
  brief_policies: string;
  brief_goal: string;
  brief_dos: string;
  brief_donts: string;
  model: string;
  timezone: string;
  manual_context: string;
  temperature: number;
  max_tokens: number;
  memory_limit: number;
  image_enabled: boolean;
  image_model: string;
  audio_enabled: boolean;
  audio_model: string;
  widget_enabled: boolean;
  widget_public_id: string;
  widget_greeting: string;
  widget_color: string;
  widget_position: string;
  is_active: boolean;
  client: Client;
  created_at: string;
  updated_at: string;
};

export type Provider = {
  provider: string;
  label: string;
  configured: boolean;
  api_key_masked: string;
};

export type ProviderTest = { ok: boolean; message: string; models: string[] };

export type KnowledgeDocument = {
  id: string;
  filename: string;
  status: "processed" | "error" | "pending";
  error_message: string | null;
  character_count: number;
  created_at: string;
};

export type QAPair = { id: string; question: string; answer: string };

export type ToolParam = { name: string; type: "string" | "number" | "integer" | "boolean"; description: string; required: boolean };
export type McpCachedTool = { name: string; description: string; input_schema?: Record<string, unknown> };
export type AgentTool = {
  id: string;
  agent_id: string;
  type: "http" | "mcp";
  name: string;
  description: string;
  enabled: boolean;
  url: string;
  http_method: string;
  prompt_instructions: string;
  body_params: ToolParam[];
  query_params: ToolParam[];
  timeout_seconds: number;
  transport: "sse" | "streamable_http";
  cached_tools: McpCachedTool[];
  tools_cached_at: string | null;
  has_headers: boolean;
  created_at: string;
  updated_at: string;
};
export type ToolCallMeta = { name: string; arguments: Record<string, unknown>; result_preview: string; is_error: boolean };

export type Source = { id: string; filename: string; excerpt: string };
export type Message = { id: string; role: "user" | "assistant"; content: string; sources: Source[]; tool_calls?: ToolCallMeta[] | null; sender_type: "visitor" | "ai" | "human"; sender_name: string | null; media_kind?: string | null; media_mime?: string | null; media_filename?: string | null; media_url?: string | null; created_at: string };
export type ConversationLead = { id: string; name: string | null; phone: string | null; email: string | null; interest: string | null; budget: string | null; preferred_contact_time: string | null; status: string };
export type LeadStatus = "new" | "qualified" | "follow_up" | "won" | "lost";
export type Lead = {
  id: string;
  agency_id: string;
  client_id: string;
  agent_id: string;
  name: string | null;
  phone: string | null;
  email: string | null;
  interest: string | null;
  budget: string | null;
  preferred_contact_time: string | null;
  notes: string | null;
  source: string;
  status: LeadStatus;
  next_follow_up_at: string | null;
  created_at: string;
  updated_at: string;
};
export type LeadDetail = Lead & {
  conversations: { conversation_id: string; channel: string; title: string; created_at: string }[];
};

export type ConversationInbox = {
  id: string;
  agent_id: string;
  agent_name: string;
  client_id: string;
  title: string;
  contact_name: string | null;
  channel: string;
  mode: "ai" | "human";
  preview: string;
  unread: boolean;
  unread_count: number;
  updated_at: string;
};
export type Conversation = {
  id: string;
  client_id: string;
  agent_id: string;
  title: string;
  mode: "ai" | "human";
  channel: string;
  external_chat_id: string | null;
  contact_name: string | null;
  created_at: string;
  updated_at: string;
  preview?: string;
  messages?: Message[];
  lead: ConversationLead | null;
};

export type WhatsAppChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  status: "disconnected" | "connecting" | "qr" | "connected" | "reconnecting" | "error";
  phone_number: string | null;
  display_name: string | null;
  qr_code: string | null;
  last_error: string | null;
  is_enabled: boolean;
  has_session: boolean;
  last_connected_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WhatsAppCloudChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  status: "disconnected" | "connected" | "error";
  phone_number: string | null;
  display_name: string | null;
  phone_number_id: string;
  waba_id: string | null;
  has_access_token: boolean;
  has_app_secret: boolean;
  webhook_url: string;
  webhook_verify_token: string;
  last_error: string | null;
  is_enabled: boolean;
  last_connected_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PortalPublic = {
  client_name: string;
  portal_title: string;
  portal_slug: string;
  agency_name: string;
  agency_brand_color: string;
  agency_logo_url: string | null;
};

export type RestaurantProfile = {
  id: string;
  client_id: string;
  address: string;
  phone: string;
  currency: string;
  opening_hours: Record<string, string>;
  created_at: string;
  updated_at: string;
};

export type MenuOption = { id: string; name: string; price: string | number; is_available: boolean; position: number };
export type MenuProduct = {
  id: string;
  client_id: string;
  category_id: string;
  name: string;
  description: string;
  price: string | number;
  image_url: string | null;
  is_available: boolean;
  position: number;
  variants: MenuOption[];
  extras: MenuOption[];
};
export type MenuCategory = { id: string; client_id: string; name: string; position: number; is_active: boolean; products: MenuProduct[] };
export type RestaurantModalities = { id: string; client_id: string; dine_in_enabled: boolean; pickup_enabled: boolean; delivery_enabled: boolean; delivery_whatsapp: string | null; created_at: string; updated_at: string };
export type DeliveryZone = { id: string; client_id: string; name: string; fee: string | number; minimum_order: string | number; estimated_minutes: number; is_active: boolean; created_at: string; updated_at: string };
export type RestaurantPaymentMethod = { id: string; client_id: string; method: "cash" | "yape" | "plin" | "transfer" | "card" | "other"; display_name: string; instructions: string; account_name: string; account_number: string; qr_image_url: string | null; receipt_required: boolean; is_active: boolean; created_at: string; updated_at: string };
export type RestaurantPaymentMailbox = { id: string; client_id: string; email: string; imap_host: string; imap_port: number; imap_ssl: boolean; has_app_password: boolean; is_enabled: boolean; connection_status: string; last_checked_at: string | null; last_error: string | null };
export type RestaurantStaff = { id: string; client_id: string; user_id: string; role: "admin" | "cashier" | "waiter" | "kitchen" | "delivery"; is_active: boolean; user: { id: string; name: string; email: string; phone: string | null; role: string }; created_at: string; updated_at: string };
export type RestaurantStaffCandidate = { id: string; name: string; email: string; phone: string | null; role: string };
export type RestaurantWelcomeFlyer = { id: string; client_id: string; filename: string; mime_type: string; enabled: boolean; message: string; image_url: string; created_at: string; updated_at: string };
export type RestaurantOnboardingAgent = { id: string; client_id: string; name: string; personality: string; instructions: string; widget_greeting: string; is_active: boolean };
export type RestaurantReadiness = { profile: boolean; menu: boolean; modalities: boolean; payments: boolean; staff: boolean; agent: boolean; ready: boolean; status: "incomplete" | "ready_to_activate" | "agent_active" };
export type RestaurantOnboarding = { profile: RestaurantProfile; welcome_flyer: RestaurantWelcomeFlyer | null; categories: MenuCategory[]; modalities: RestaurantModalities; delivery_zones: DeliveryZone[]; payment_methods: RestaurantPaymentMethod[]; payment_mailbox: RestaurantPaymentMailbox | null; staff: RestaurantStaff[]; staff_candidates: RestaurantStaffCandidate[]; agent: RestaurantOnboardingAgent | null; readiness: RestaurantReadiness };
