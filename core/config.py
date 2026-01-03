"""代码审查系统配置管理。

支持从 YAML、JSON 和环境变量加载配置。
"""

import json
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 提供商配置。"""
    
    provider: str = Field(default="deepseek", description="LLM provider name")
    model: str = Field(default="deepseek-chat", description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key for the provider")
    base_url: Optional[str] = Field(default=None, description="Base URL for API")
    temperature: float = Field(default=0.7, description="Temperature for LLM responses")


class SystemConfig(BaseModel):
    """系统配置。"""
    
    workspace_root: Path = Field(default=Path.cwd(), description="Workspace root path")
    assets_dir: Path = Field(default=Path("assets_cache"), description="Assets cache directory")
    timeout_seconds: int = Field(default=600, description="Analysis timeout in seconds")
    asset_key: Optional[str] = Field(default=None, description="Asset key for repository-specific assets")
    max_concurrent_llm_requests: int = Field(default=5, ge=1, description="Maximum concurrent LLM API requests")
    max_expert_rounds: int = Field(default=20, ge=1, description="Maximum rounds for expert analysis (circuit breaker)")


class Config(BaseModel):
    """主配置类。"""
    
    llm: LLMConfig = Field(default_factory=LLMConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    
    @classmethod
    def load_default(cls) -> "Config":
        """加载默认配置（优先环境变量，其次配置文件，最后默认值）。"""
        # Try to load from config files first
        config = cls._load_from_files()
        
        # Override with environment variables if present
        config = cls._load_from_env(config)
        
        return config
    
    @classmethod
    def load_from_file(cls, config_path: Path) -> "Config":
        """从指定文件加载配置（支持 YAML/JSON）。
        
        Args:
            config_path: 配置文件路径。
        
        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 格式不支持或无效。
        """
        config_path = Path(config_path).resolve()
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        suffix = config_path.suffix.lower()
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                if suffix in [".yaml", ".yml"]:
                    try:
                        import yaml
                        data = yaml.safe_load(f)
                    except ImportError:
                        raise ImportError(
                            "PyYAML is required for YAML config files. "
                            "Install with: pip install pyyaml"
                        )
                elif suffix == ".json":
                    data = json.load(f)
                else:
                    raise ValueError(f"Unsupported config file format: {suffix}")
            
            # Validate and create config
            return cls(**data)
        
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
        except Exception as e:
            raise ValueError(f"Error loading config file: {e}")
    
    @classmethod
    def _load_from_files(cls) -> "Config":
        """从标准位置加载配置文件（config.yaml/config.json/.env）。"""
        config_paths = [
            Path("config.yaml"),
            Path("config.yml"),
            Path("config.json"),
        ]
        
        for config_path in config_paths:
            if config_path.exists():
                try:
                    return cls.load_from_file(config_path)
                except Exception as e:
                    # Log warning but continue to try other files
                    print(f"Warning: Could not load {config_path}: {e}")
                    continue
        
        # Try to load from .env file if python-dotenv is available
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass  # python-dotenv not installed, skip
        
        # Return default config if no files found
        return cls()
    
    @classmethod
    def _load_from_env(cls, config: "Config") -> "Config":
        """用环境变量覆盖配置。"""
        # LLM configuration from environment
        llm_config = config.llm.model_copy() if hasattr(config.llm, 'model_copy') else LLMConfig()
        
        # Load provider, model, base_url from environment (if set)
        if os.getenv("LLM_PROVIDER"):
            llm_config.provider = os.getenv("LLM_PROVIDER", llm_config.provider)
        if os.getenv("LLM_MODEL"):
            llm_config.model = os.getenv("LLM_MODEL", llm_config.model)
        if os.getenv("LLM_BASE_URL"):
            llm_config.base_url = os.getenv("LLM_BASE_URL", llm_config.base_url)
        
        # Load API key from environment variables
        # Priority: LLM_API_KEY > provider-specific keys (DEEPSEEK_API_KEY, ZHIPUAI_API_KEY)
        if os.getenv("LLM_API_KEY"):
            llm_config.api_key = os.getenv("LLM_API_KEY", llm_config.api_key)
        elif os.getenv("DEEPSEEK_API_KEY") and llm_config.provider == "deepseek":
            # Only use DEEPSEEK_API_KEY if provider is already set to "deepseek"
            llm_config.api_key = os.getenv("DEEPSEEK_API_KEY")
        elif os.getenv("ZHIPUAI_API_KEY") and llm_config.provider == "zhipuai":
            # Only use ZHIPUAI_API_KEY if provider is already set to "zhipuai"
            llm_config.api_key = os.getenv("ZHIPUAI_API_KEY")
        if os.getenv("LLM_TEMPERATURE"):
            try:
                llm_config.temperature = float(os.getenv("LLM_TEMPERATURE", str(llm_config.temperature)))
            except ValueError:
                pass
        
        # System configuration from environment
        system_config = config.system.model_copy() if hasattr(config.system, 'model_copy') else SystemConfig()
        
        if os.getenv("WORKSPACE_ROOT"):
            system_config.workspace_root = Path(os.getenv("WORKSPACE_ROOT"))
        if os.getenv("ASSETS_DIR"):
            system_config.assets_dir = Path(os.getenv("ASSETS_DIR"))
        if os.getenv("TIMEOUT_SECONDS"):
            try:
                system_config.timeout_seconds = int(os.getenv("TIMEOUT_SECONDS", str(system_config.timeout_seconds)))
            except ValueError:
                pass
        if os.getenv("MAX_CONCURRENT_LLM_REQUESTS"):
            try:
                system_config.max_concurrent_llm_requests = int(os.getenv("MAX_CONCURRENT_LLM_REQUESTS", str(system_config.max_concurrent_llm_requests)))
            except ValueError:
                pass
        if os.getenv("MAX_EXPERT_ROUNDS"):
            try:
                system_config.max_expert_rounds = int(os.getenv("MAX_EXPERT_ROUNDS", str(system_config.max_expert_rounds)))
            except ValueError:
                pass
        
        return cls(llm=llm_config, system=system_config)
    
    def save_to_file(self, config_path: Path) -> None:
        """保存配置到文件（格式由扩展名决定）。
        
        Raises:
            ValueError: 格式不支持。
        """
        config_path = Path(config_path).resolve()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        suffix = config_path.suffix.lower()
        config_dict = self.model_dump()
        
        with open(config_path, "w", encoding="utf-8") as f:
            if suffix in [".yaml", ".yml"]:
                try:
                    import yaml
                    yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)
                except ImportError:
                    raise ImportError(
                        "PyYAML is required for YAML config files. "
                        "Install with: pip install pyyaml"
                    )
            elif suffix == ".json":
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            else:
                raise ValueError(f"Unsupported config file format: {suffix}")

