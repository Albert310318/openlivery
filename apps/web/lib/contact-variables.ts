// Contact and agent properties a saved reply or a WhatsApp template can pull
// in. A saved reply fills them from the open conversation the moment it is
// inserted; a template fills them from the contact when it is sent, so the
// value never travels empty the way a free-typed variable can.
export const CONTACT_VARIABLES = ["contact_name", "contact_phone", "contact_email", "agent_name"] as const;
export type ContactVariable = (typeof CONTACT_VARIABLES)[number];
export type ContactValues = Record<ContactVariable, string>;

export const EMPTY_CONTACT_VALUES: ContactValues = {
  contact_name: "",
  contact_phone: "",
  contact_email: "",
  agent_name: "",
};

// Stand-ins shown in previews and sent to Meta as a template's review sample.
export const CONTACT_EXAMPLES: ContactValues = {
  contact_name: "María",
  contact_phone: "+57 300 123 4567",
  contact_email: "maria@correo.com",
  agent_name: "Ana",
};

export function isContactVariable(name: string): name is ContactVariable {
  return (CONTACT_VARIABLES as readonly string[]).includes(name);
}
