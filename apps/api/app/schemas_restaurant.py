import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class RestaurantORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RestaurantProfileUpdate(BaseModel):
    address: str = Field(default="", max_length=1000)
    phone: str = Field(default="", max_length=80)
    currency: str = Field(default="PEN", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    opening_hours: dict[str, str] = Field(default_factory=dict)


class RestaurantProfileOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    address: str
    phone: str
    currency: str
    opening_hours: dict[str, str]
    created_at: datetime
    updated_at: datetime


class RestaurantWelcomeFlyerOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    filename: str
    mime_type: str
    enabled: bool
    message: str
    image_url: str
    created_at: datetime
    updated_at: datetime


class MenuCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    position: int = Field(default=0, ge=0)
    is_active: bool = True


class MenuCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    position: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class MenuOptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    is_available: bool = True
    position: int = Field(default=0, ge=0)


class MenuOptionUpdate(MenuOptionCreate):
    pass


class MenuProductCreate(BaseModel):
    category_id: uuid.UUID
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=5000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    image_url: str | None = Field(default=None, max_length=2000)
    is_available: bool = True
    position: int = Field(default=0, ge=0)
    variants: list[MenuOptionCreate] = Field(default_factory=list)
    extras: list[MenuOptionCreate] = Field(default_factory=list)


class MenuProductUpdate(BaseModel):
    category_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=5000)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    image_url: str | None = Field(default=None, max_length=2000)
    is_available: bool | None = None
    position: int | None = Field(default=None, ge=0)
    variants: list[MenuOptionCreate] | None = None
    extras: list[MenuOptionCreate] | None = None


class MenuOptionOut(RestaurantORM):
    id: uuid.UUID
    name: str
    price: Decimal
    is_available: bool
    position: int


class MenuProductOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str
    price: Decimal
    image_url: str | None
    is_available: bool
    position: int
    variants: list[MenuOptionOut] = []
    extras: list[MenuOptionOut] = []


class MenuCategoryOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    position: int
    is_active: bool
    products: list[MenuProductOut] = []


class RestaurantModalitiesUpdate(BaseModel):
    dine_in_enabled: bool = False
    pickup_enabled: bool = False
    delivery_enabled: bool = False
    delivery_whatsapp: str | None = Field(default=None, max_length=80)


class RestaurantModalitiesOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    dine_in_enabled: bool
    pickup_enabled: bool
    delivery_enabled: bool
    delivery_whatsapp: str | None
    created_at: datetime
    updated_at: datetime


class DeliveryZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    minimum_order: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    estimated_minutes: int = Field(default=45, ge=1, le=1440)
    is_active: bool = True


class DeliveryZoneUpdate(DeliveryZoneCreate):
    pass


class DeliveryZoneOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    fee: Decimal
    minimum_order: Decimal
    estimated_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


PaymentMethodCode = Literal["cash", "yape", "plin", "transfer", "card", "other"]


class RestaurantPaymentMethodCreate(BaseModel):
    method: PaymentMethodCode
    display_name: str = Field(default="", max_length=180)
    instructions: str = Field(default="", max_length=2000)
    account_name: str = Field(default="", max_length=180)
    account_number: str = Field(default="", max_length=180)
    qr_image_url: str | None = Field(default=None, max_length=2000)
    receipt_required: bool = False
    is_active: bool = True


class RestaurantPaymentMethodUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=180)
    instructions: str | None = Field(default=None, max_length=2000)
    account_name: str | None = Field(default=None, max_length=180)
    account_number: str | None = Field(default=None, max_length=180)
    qr_image_url: str | None = Field(default=None, max_length=2000)
    receipt_required: bool | None = None
    is_active: bool | None = None


class RestaurantPaymentMethodOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    method: PaymentMethodCode
    display_name: str
    instructions: str
    account_name: str
    account_number: str
    qr_image_url: str | None
    receipt_required: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RestaurantPaymentMailboxUpdate(BaseModel):
    email: EmailStr
    imap_host: str = Field(default="imap.gmail.com", min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_ssl: bool = True
    app_password: str | None = Field(default=None, min_length=1, max_length=255)
    is_enabled: bool = True


class RestaurantPaymentMailboxOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    email: EmailStr
    imap_host: str
    imap_port: int
    imap_ssl: bool
    has_app_password: bool
    is_enabled: bool
    connection_status: str
    last_checked_at: datetime | None
    last_error: str | None


StaffRole = Literal["admin", "cashier", "waiter", "kitchen", "delivery"]


class RestaurantStaffCreate(BaseModel):
    user_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: StaffRole
    is_active: bool = True

    @model_validator(mode="after")
    def validate_new_user_fields(self):
        if not self.user_id and (not self.name or not self.email or not self.password):
            raise ValueError("Name, email and password are required for a new user")
        return self


class RestaurantStaffUpdate(BaseModel):
    role: StaffRole | None = None
    is_active: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class RestaurantStaffUserOut(RestaurantORM):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    role: str


class RestaurantStaffOut(RestaurantORM):
    id: uuid.UUID
    client_id: uuid.UUID
    user_id: uuid.UUID
    role: StaffRole
    is_active: bool
    development_verification_bypassed: bool = False
    user: RestaurantStaffUserOut
    created_at: datetime
    updated_at: datetime


class RestaurantAgentOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    personality: str
    instructions: str
    widget_greeting: str
    is_active: bool


class RestaurantReadinessOut(BaseModel):
    profile: bool
    menu: bool
    modalities: bool
    payments: bool
    staff: bool
    agent: bool
    ready: bool
    status: Literal["incomplete", "ready_to_activate", "agent_active"]


class RestaurantOnboardingOut(BaseModel):
    profile: RestaurantProfileOut
    welcome_flyer: RestaurantWelcomeFlyerOut | None = None
    categories: list[MenuCategoryOut]
    modalities: RestaurantModalitiesOut
    delivery_zones: list[DeliveryZoneOut]
    payment_methods: list[RestaurantPaymentMethodOut]
    payment_mailbox: RestaurantPaymentMailboxOut | None
    staff: list[RestaurantStaffOut]
    staff_candidates: list[RestaurantStaffUserOut]
    agent: RestaurantAgentOut | None
    readiness: RestaurantReadinessOut
