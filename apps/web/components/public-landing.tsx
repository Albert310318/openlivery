"use client";

import Link from "next/link";
import {
  ArrowRight,
  Bot,
  Building2,
  Check,
  ChevronDown,
  Clock3,
  Handshake,
  Hotel,
  MessageCircle,
  MessageSquareText,
  Rocket,
  Sparkles,
  Target,
  Utensils,
  UsersRound,
  X,
} from "lucide-react";
import { BrandLogo } from "@/components/brand";

const benefits = [
  { icon: Clock3, title: "Atención 24/7", copy: "Responde consultas y mantiene activa la conversación incluso fuera de horario." },
  { icon: MessageCircle, title: "WhatsApp conectado", copy: "Atiende desde el canal donde tus clientes ya están conversando." },
  { icon: Target, title: "Captación de leads", copy: "Convierte conversaciones en oportunidades con datos útiles para vender mejor." },
  { icon: Clock3, title: "Seguimiento", copy: "Organiza próximos pasos para que ninguna oportunidad se quede sin respuesta." },
  { icon: Handshake, title: "Derivación a asesor", copy: "Pasa la conversación a una persona cuando el cliente necesita atención humana." },
  { icon: Sparkles, title: "Automatización", copy: "Configura agentes con contexto, conocimiento y herramientas para tu negocio." },
];

const moduleCatalog = [
  { code: "ai_agent", label: "Agente de IA" },
  { code: "leads", label: "Gestión de leads" },
  { code: "advisor_handoff", label: "Derivación a asesor" },
  { code: "catalog", label: "Catálogo" },
  { code: "appointments", label: "Citas" },
  { code: "reservations", label: "Reservas" },
  { code: "orders", label: "Pedidos" },
  { code: "payments", label: "Pagos" },
  { code: "operations", label: "Operaciones" },
  { code: "delivery", label: "Delivery" },
  { code: "automations", label: "Automatizaciones" },
  { code: "post_sale", label: "Postventa" },
  { code: "analytics", label: "Analítica" },
] as const;

const plans = [
  { name: "Prueba gratuita", price: "3 días", note: "Conoce AYV sin costo", accent: false, modules: ["ai_agent", "leads", "advisor_handoff", "catalog", "appointments", "reservations", "orders", "payments", "operations", "delivery", "automations", "post_sale", "analytics"] },
  { name: "Inicial", price: "S/39", note: "", accent: false, modules: ["ai_agent", "leads", "advisor_handoff", "catalog", "analytics"] },
  { name: "Intermedio", price: "S/79", note: "", accent: true, modules: ["ai_agent", "leads", "advisor_handoff", "catalog", "appointments", "reservations", "orders", "automations", "post_sale", "analytics"] },
  { name: "Business", price: "S/139", note: "", accent: false, modules: ["ai_agent", "leads", "advisor_handoff", "catalog", "appointments", "reservations", "orders", "payments", "operations", "delivery", "automations", "post_sale", "analytics"] },
];

const moduleRows = moduleCatalog.map((module) => ({
  label: module.label,
  values: plans.map((plan) => plan.modules.includes(module.code)),
}));

function Logo() {
  return <Link className="landing-logo" href="/" aria-label="Atiende y Vende, inicio"><BrandLogo /></Link>;
}

export function PublicLanding() {
  return (
    <main className="landing-page">
      <nav className="landing-nav" aria-label="Navegación principal">
        <Logo />
        <div className="landing-nav-links">
          <a href="#como-funciona">Cómo funciona</a>
          <a href="#beneficios">Beneficios</a>
          <a href="#planes">Planes</a>
          <a href="#faq">Preguntas frecuentes</a>
        </div>
        <div className="landing-nav-actions">
          <Link href="/login" className="landing-login">Iniciar sesión</Link>
          <Link href="/registro" className="landing-button landing-button-small">Probar AYV <ArrowRight size={15} /></Link>
        </div>
      </nav>

      <section className="landing-hero">
        <div className="landing-hero-copy">
          <div className="landing-kicker"><span><Sparkles size={14} /></span> Inteligencia aplicada a las ventas</div>
          <h1>Vende más.<br /><em>Conversa mejor.</em></h1>
          <p className="landing-hero-text">Agentes de IA que atienden clientes, captan oportunidades y automatizan tus ventas, para que tu equipo se enfoque en hacer crecer el negocio.</p>
          <div className="landing-hero-actions">
            <Link href="/registro" className="landing-button">Probar AYV <ArrowRight size={17} /></Link>
            <a href="#como-funciona" className="landing-text-link">Descubre cómo funciona <ArrowRight size={16} /></a>
          </div>
          <div className="landing-hero-proof"><span className="landing-avatar-stack"><i>✓</i><i>+</i><i>∞</i></span><span>Diseñado para negocios que quieren vender sin perder conversaciones.</span></div>
        </div>
        <div className="landing-hero-visual" aria-label="Vista previa de un agente AYV atendiendo clientes">
          <div className="landing-orbit landing-orbit-one" /><div className="landing-orbit landing-orbit-two" />
          <div className="landing-dashboard-card">
            <div className="landing-card-top"><div className="landing-card-brand"><span className="landing-mini-mark">AYV</span><div><strong>AYV</strong><small>Centro de ventas</small></div></div><span className="landing-live"><i /> En vivo</span></div>
            <div className="landing-mini-metrics"><div><span>Conversaciones</span><strong>248</strong><em>+18.4%</em></div><div><span>Leads captados</span><strong>64</strong><em>+12.8%</em></div></div>
            <div className="landing-chat-window">
              <div className="landing-chat-head"><span className="landing-bot-avatar"><Bot size={16} /></span><div><strong>Agente AYV</strong><small>Responde al instante</small></div><span className="landing-chat-status" /></div>
              <div className="landing-message incoming">Hola, quisiera conocer los paquetes para este fin de semana.</div>
              <div className="landing-message outgoing">¡Hola! Con gusto te ayudo. ¿Cuántas personas viajarían?</div>
              <div className="landing-message incoming short">Seríamos 4 personas.</div>
              <div className="landing-typing"><span /><span /><span /> AYV está escribiendo</div>
            </div>
            <div className="landing-dashboard-foot"><span><MessageSquareText size={14} /> WhatsApp</span><span><UsersRound size={14} /> Nuevo lead</span><span className="landing-arrow-badge"><ArrowRight size={14} /></span></div>
          </div>
          <div className="landing-float-card landing-float-top"><span className="landing-float-icon green"><Check size={15} /></span><div><strong>Lead calificado</strong><small>Hace 2 minutos</small></div></div>
          <div className="landing-float-card landing-float-bottom"><span className="landing-float-icon purple"><Rocket size={15} /></span><div><strong>Seguimiento listo</strong><small>3 oportunidades hoy</small></div></div>
        </div>
      </section>

      <div className="landing-trust-bar"><span>AYV funciona para equipos que venden por</span><strong>WhatsApp</strong><strong>Web</strong><strong>Conversaciones</strong><strong>Seguimiento</strong></div>

      <section className="landing-section landing-how" id="como-funciona">
        <div className="landing-section-heading"><div><span className="landing-section-label">Cómo funciona</span><h2>De la primera pregunta<br />a una oportunidad real.</h2></div><p>AYV acompaña cada conversación con la velocidad de la IA y el criterio de tu negocio.</p></div>
        <div className="landing-steps">
          <article className="landing-step"><span className="landing-step-number">01</span><div className="landing-step-icon"><MessageSquareText size={23} /></div><h3>Conecta tus canales</h3><p>Integra WhatsApp y prepara tu agente con el contexto que hace único a tu negocio.</p><span className="landing-step-line" /></article>
          <article className="landing-step"><span className="landing-step-number">02</span><div className="landing-step-icon"><Bot size={23} /></div><h3>Tu agente conversa</h3><p>Responde preguntas, entiende necesidades y captura la información de cada oportunidad.</p><span className="landing-step-line" /></article>
          <article className="landing-step"><span className="landing-step-number">03</span><div className="landing-step-icon"><Handshake size={23} /></div><h3>Tu equipo convierte</h3><p>Revisa tus leads, da seguimiento y toma la conversación cuando el momento lo necesita.</p></article>
        </div>
      </section>

      <section className="landing-section landing-benefits" id="beneficios">
        <div className="landing-section-heading centered"><span className="landing-section-label">Todo lo que necesitas para vender mejor</span><h2>Menos tareas repetitivas.<br /><em>Más conversaciones que avanzan.</em></h2><p>Un sistema simple para ordenar la atención y convertir la intención en acción.</p></div>
        <div className="landing-benefits-grid">{benefits.map(({ icon: Icon, title, copy }) => <article className="landing-benefit" key={title}><span className="landing-benefit-icon"><Icon size={20} /></span><h3>{title}</h3><p>{copy}</p></article>)}</div>
      </section>

      <section className="landing-businesses"><div className="landing-businesses-copy"><span className="landing-section-label">Un agente para cada negocio</span><h2>Tu forma de vender<br />también merece inteligencia.</h2><p>AYV se adapta a las preguntas, ritmos y oportunidades de cada MYPE.</p><Link href="/login" className="landing-text-link">Empieza a explorar <ArrowRight size={16} /></Link></div><div className="landing-business-grid"><article><span><Building2 size={19} /></span><strong>Inmobiliarias</strong><small>Califica consultas y agenda el siguiente paso.</small></article><article><span><Hotel size={19} /></span><strong>Turismo</strong><small>Atiende viajeros y convierte interés en reservas.</small></article><article><span><Utensils size={19} /></span><strong>Restaurantes</strong><small>Responde rápido y mantiene el flujo de pedidos.</small></article><article><span><UsersRound size={19} /></span><strong>Otras MYPE</strong><small>Ordena tus conversaciones y vende con foco.</small></article></div></section>

      <section className="landing-section landing-pricing" id="planes">
        <div className="landing-section-heading centered"><span className="landing-section-label">Planes simples, crecimiento claro</span><h2>Empieza con lo que necesitas.<br /><em>Crece cuando estés listo.</em></h2><p>Prueba AYV y encuentra el nivel adecuado para tu operación comercial.</p></div>
        <div className="landing-plans">{plans.map((plan) => { const visibleModules = plan.modules.slice(0, 6); const extraModules = plan.modules.length - visibleModules.length; return <article className={`landing-plan ${plan.accent ? "featured" : ""}`} key={plan.name}>{plan.accent && <span className="landing-plan-badge">Más elegido</span>}<h3>{plan.name}</h3><div className="landing-plan-price">{plan.price}{plan.name !== "Prueba gratuita" && <small> / mes</small>}</div><p>{plan.note}</p><div className="landing-plan-divider" /><span className="landing-plan-caption">Incluye</span><ul>{visibleModules.map((moduleCode) => <li key={moduleCode}><Check size={15} />{moduleCatalog.find((module) => module.code === moduleCode)?.label}</li>)}{extraModules > 0 && <li className="landing-plan-more">+ {extraModules} funciones más</li>}</ul><Link href="/registro" className={plan.accent ? "landing-button" : "landing-button landing-button-outline"}>{plan.name === "Prueba gratuita" ? "Probar gratis" : "Elegir plan"} <ArrowRight size={15} /></Link></article>; })}</div>
        <div className="landing-promo"><span><Sparkles size={17} /></span><div><strong>¿Tienes un código promocional?</strong><small>Disponible próximamente</small></div><span className="landing-promo-placeholder">Código promocional <ArrowRight size={15} /></span></div>
        <div className="landing-comparison"><div className="landing-comparison-title"><strong>Comparación de módulos</strong><span>Los módulos disponibles en AYV</span></div><div className="landing-comparison-table"><div className="landing-comparison-row header"><span>Módulo</span>{plans.map((plan) => <strong key={plan.name}>{plan.name}</strong>)}</div>{moduleRows.map((row) => <div className="landing-comparison-row" key={row.label}><span>{row.label}</span>{row.values.map((included, index) => <span key={`${row.label}-${plans[index].name}`} className={included ? "included" : "not-included"}>{included ? <Check size={15} /> : <X size={15} />}</span>)}</div>)}</div></div>
      </section>

      <section className="landing-faq landing-section" id="faq"><div className="landing-section-heading centered"><span className="landing-section-label">Preguntas frecuentes</span><h2>Todo claro para<br /><em>empezar hoy.</em></h2></div><div className="landing-faq-list"><details open><summary>¿Qué es AYV?<ChevronDown size={18} /></summary><p>AYV es una plataforma de agentes de IA para atender clientes, captar leads y automatizar el seguimiento comercial en un solo lugar.</p></details><details><summary>¿Necesito saber de tecnología? <ChevronDown size={18} /></summary><p>No. La experiencia está pensada para que puedas configurar tu agente y comenzar a conversar sin un equipo técnico.</p></details><details><summary>¿Puedo usar AYV con WhatsApp? <ChevronDown size={18} /></summary><p>Sí. AYV contempla la conexión de WhatsApp para que tu agente atienda a tus clientes en el canal que ya utilizan.</p></details><details><summary>¿Qué pasa cuando necesito intervenir? <ChevronDown size={18} /></summary><p>Puedes revisar la oportunidad y derivar la conversación a un asesor cuando se necesita atención humana.</p></details></div></section>

      <section className="landing-final-cta"><div><span className="landing-section-label">El siguiente cliente puede estar a un mensaje de distancia</span><h2>Haz que cada conversación<br /><em>cuente.</em></h2><p>Empieza a construir una atención más rápida, ordenada y lista para vender.</p></div><Link href="/registro" className="landing-button landing-button-light">Probar AYV <ArrowRight size={17} /></Link></section>

      <footer className="landing-footer"><Logo /><span>Inteligencia aplicada a las ventas.</span><div><a href="#como-funciona">Cómo funciona</a><a href="#planes">Planes</a><Link href="/login">Iniciar sesión</Link></div><small>© {new Date().getFullYear()} Atiende y Vende. Todos los derechos reservados.</small></footer>
    </main>
  );
}
