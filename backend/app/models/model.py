from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, func
from app.database import Base


class Model(Base):
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    model_id = Column(String(200), nullable=False)
    display_name = Column(String(200), default="")
    is_active = Column(Boolean, default=True)
    is_multimodal = Column(Boolean, default=False)
    model_type = Column(String(20), default="text")  # 主类型 text/image/video/tts/embedding（路由按它匹配）
    # 模型支持的全部类型（逗号分隔，如 "text,image"）：仅用于展示，为空时回退 model_type
    model_types = Column(String(120), default="")
    # 模型能力：识图 / 搜索支持 / 工具调用
    supports_vision = Column(Boolean, default=False)
    supports_search = Column(Boolean, default=False)
    supports_tools = Column(Boolean, default=False)
    # 能力推断版本：存量行由迁移置 0（启动时重算），新建行直接为 2
    cap_version = Column(Integer, default=2)
    context_window = Column(Integer, nullable=True)
    temperature = Column(Float, nullable=True)
    price_input = Column(Float, nullable=True)   # per 1M tokens
    price_output = Column(Float, nullable=True)  # per 1M tokens
    status = Column(String(20), default="untested")  # untested/active/inactive
    last_error = Column(String(500), default="")
    created_at = Column(DateTime, server_default=func.now())
