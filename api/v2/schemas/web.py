import json

from typing import Any

from pydantic import BaseModel, Field, model_validator


_MAX_BLOCK_DATA_SIZE = 4 * 1024 * 1024


class WebBlockBase(BaseModel):
    type: str = Field(..., max_length=64)
    order: int
    data: dict[str, Any]

    @model_validator(mode="after")
    def _check_data_size(self) -> "WebBlockBase":
        if len(json.dumps(self.data, ensure_ascii=False)) > _MAX_BLOCK_DATA_SIZE:
            raise ValueError(f"Размер data блока не должен превышать {_MAX_BLOCK_DATA_SIZE // 1024} КБ")
        return self


class WebBlockResponse(WebBlockBase):
    id: str

    class Config:
        from_attributes = True


class WebTheme(BaseModel):
    tokens: dict[str, Any]


class WebPageThemeResponse(BaseModel):
    slug: str
    variant_key: str = "default"
    tokens: dict[str, Any] = Field(default_factory=dict)


class WebPageThemeUpdate(BaseModel):
    tokens: dict[str, Any]


class WebPageVariantSummary(BaseModel):
    key: str = Field(..., max_length=64)
    name: str = Field(..., max_length=255)
    is_active: bool = False


class WebPageSaveResponse(BaseModel):
    slug: str
    variant_key: str = "default"
    active_variant_key: str = "default"
    variants: list[WebPageVariantSummary] = Field(default_factory=list)


class WebPageResponse(BaseModel):
    slug: str
    blocks: list[WebBlockResponse]
    theme: WebTheme | None = None
    variant_key: str = "default"
    active_variant_key: str = "default"
    variants: list[WebPageVariantSummary] = Field(default_factory=list)


class WebPageUpdate(BaseModel):
    blocks: list[WebBlockBase]
    theme: WebTheme | None = None


class WebPageVariantCreate(BaseModel):
    key: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=255)
    from_variant_key: str | None = Field(default=None, max_length=64)


class WebPageVariantUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    make_active: bool | None = None


class WebPageVariantsResponse(BaseModel):
    slug: str
    active_variant_key: str = "default"
    current_variant_key: str = "default"
    variants: list[WebPageVariantSummary] = Field(default_factory=list)


class WebUploadResponse(BaseModel):
    url: str
