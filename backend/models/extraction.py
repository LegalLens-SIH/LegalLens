"""
Data contracts for the structured-extraction layer.

These models describe a NORMALIZED, EVIDENCE-LINKED view of what OCR found
on a package - product identity, pricing, quantity, business declarations,
dates, identification, origin, consumer care, food information, and
free-form warnings/declarations. Nothing here makes, implies, or stores a
compliance decision - that remains entirely backend/services/
compliance_engine.py's job, unchanged. See backend/services/
structured_extraction.py's module docstring for how this is built.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class EvidenceField(BaseModel):
    """One extracted value, always traceable back to the OCR text it came
    from. `ocr_confidence` is PaddleOCR's own recognition confidence for
    the underlying text (never touched by the extraction step);
    `extraction_confidence` is this module's own confidence that it parsed/
    interpreted that text correctly - a different question, kept separate
    per this task's explicit requirement. `source` distinguishes a value
    the deterministic parser resolved on its own from one the Gemini
    AI/vision fallback proposed (see backend/ocr/gemini_service.py) -
    never the compliance engine, which never runs during extraction."""

    value: Any = None
    raw_text: Optional[str] = None
    ocr_confidence: float = Field(0, ge=0, le=1)
    extraction_confidence: float = Field(0, ge=0, le=1)
    evidence_region_ids: List[str] = Field(default_factory=list)
    source: str = Field("ocr_parser", description="'ocr_parser' or 'ai_fallback'")


class MoneyField(EvidenceField):
    currency: Optional[str] = "INR"


class QuantityField(EvidenceField):
    unit: Optional[str] = None


class Product(BaseModel):
    name: Optional[EvidenceField] = None
    brand: Optional[EvidenceField] = None


class Pricing(BaseModel):
    maximum_retail_price_mrp: MoneyField = Field(default_factory=MoneyField)
    unit_sale_price: MoneyField = Field(default_factory=MoneyField)


class BusinessDetails(BaseModel):
    manufacturer: Optional[EvidenceField] = None
    packer: Optional[EvidenceField] = None
    importer: Optional[EvidenceField] = None
    marketer: Optional[EvidenceField] = None
    distributor: Optional[EvidenceField] = None


class Dates(BaseModel):
    manufacture_or_packing_date: Optional[EvidenceField] = None
    expiry_or_best_before: Optional[EvidenceField] = None


class Identification(BaseModel):
    batch_or_lot_number: Optional[EvidenceField] = None
    barcode: Optional[EvidenceField] = None
    qr_code: Optional[EvidenceField] = None


class Origin(BaseModel):
    country_of_origin: Optional[EvidenceField] = None


class ConsumerCare(BaseModel):
    details: Optional[EvidenceField] = None


class FoodInformation(BaseModel):
    fssai: Optional[EvidenceField] = None
    ingredients: Optional[EvidenceField] = None
    nutrition: Optional[EvidenceField] = None


class ExtractionMetadata(BaseModel):
    overall_confidence: float = Field(0, ge=0, le=1)
    extraction_method: str = "hybrid"
    ai_assisted: bool = False
    ai_fields_used: List[str] = Field(default_factory=list)


class StructuredExtraction(BaseModel):
    """Top-level structured-extraction result for one scan. Purely
    informational - see this module's docstring."""

    product: Product = Field(default_factory=Product)
    pricing: Pricing = Field(default_factory=Pricing)
    quantity: QuantityField = Field(default_factory=QuantityField)
    business_details: BusinessDetails = Field(default_factory=BusinessDetails)
    dates: Dates = Field(default_factory=Dates)
    identification: Identification = Field(default_factory=Identification)
    origin: Origin = Field(default_factory=Origin)
    consumer_care: ConsumerCare = Field(default_factory=ConsumerCare)
    food_information: FoodInformation = Field(default_factory=FoodInformation)
    warnings_and_declarations: List[EvidenceField] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)
