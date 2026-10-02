# core/config_manager.py
# -*- coding: utf-8 -*-
"""全局配置（跨所有小说工程共用）：LLM、Embedding、代理、workspace 根目录。

每本小说自己的续写设定不存在这里，而是存在各工程目录下的 project.json
（见 core.project_manager）。
"""
import json
import logging
import os
import tempfile
import threading
from copy import deepcopy

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_LLM_CONFIG_NAME = "Ollama Qwen3 8B"
DEFAULT_EMBEDDING_CONFIG_NAME = "本地bge"

DEFAULT_CONFIG = {
    "workspace_root": "",
    "default_llm_config_name": DEFAULT_LLM_CONFIG_NAME,
    "last_llm_config_name": DEFAULT_LLM_CONFIG_NAME,
    "last_embedding_config_name": DEFAULT_EMBEDDING_CONFIG_NAME,
    "llm_configs": {
        "Ollama Qwen3 8B": {
            "api_key": "",
            "base_url": "http://localhost:11434/v1",
            "model_name": "qwen3:8b",
            "temperature": 0.8,
            "max_tokens": 8192,
            "timeout": 2400,
            "interface_format": "Ollama",
        },
        "DeepSeek": {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-chat",
            "temperature": 0.8,
            "max_tokens": 8192,
            "timeout": 600,
            "interface_format": "DeepSeek",
        },
        "OpenAI": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o",
            "temperature": 0.8,
            "max_tokens": 8192,
            "timeout": 600,
            "interface_format": "OpenAI",
        },
    },
    "embedding_configs": {
        "本地bge": {
            "api_key": "",
            "base_url": "http://127.0.0.1:11435",
            "model_name": "bge-m3",
            "retrieval_k": 4,
            "interface_format": "本地bge",
        },
        "离线哈希": {
            "api_key": "",
            "base_url": "",
            "model_name": "",
            "retrieval_k": 4,
            "interface_format": "离线哈希",
        },
        "Ollama": {
            "api_key": "",
            "base_url": "http://localhost:11434",
            "model_name": "bge-m3",
            "retrieval_k": 4,
            "interface_format": "Ollama",
        },
    },
    "proxy_setting": {
        "proxy_url": "127.0.0.1",
        "proxy_port": "",
        "enabled": False,
    },
    "continuation_defaults": {
        "chars_per_beat": 600,
        "temperature": 0.8,
        "beats_per_chapter": 6,
    },
}

_config_lock = threading.RLock()


def get_default_config() -> dict:
    """返回默认配置的深拷贝，避免调用方改到全局模板。"""
    return deepcopy(DEFAULT_CONFIG)


def _merge_missing_values(target: dict, defaults: dict) -> dict:
    """只补缺失键，不覆盖用户已有值。"""
    for key, value in defaults.items():
        if key not in target:
            target[key] = deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_missing_values(target[key], value)
    return target


def normalize_config(config_data: dict) -> dict:
    """补齐缺失的配置结构，并校正失效的“当前配置”指针。"""
    if not isinstance(config_data, dict):
        config_data = {}
    defaults = get_default_config()

    # 模型配置的键名由用户管理：只有整个分组为空时才恢复默认项。
    # 若逐键合并默认值，用户删除默认配置后，下次加载会被悄悄加回来。
    for section in ("llm_configs", "embedding_configs"):
        if not isinstance(config_data.get(section), dict) or not config_data[section]:
            config_data[section] = deepcopy(defaults[section])

    for section in ("proxy_setting", "continuation_defaults"):
        if not isinstance(config_data.get(section), dict):
            config_data[section] = {}
        _merge_missing_values(config_data[section], defaults[section])

    llm_configs = config_data["llm_configs"]
    last_llm = config_data.get("last_llm_config_name")
    if last_llm not in llm_configs:
        last_llm = (DEFAULT_LLM_CONFIG_NAME if DEFAULT_LLM_CONFIG_NAME in llm_configs
                    else next(iter(llm_configs), ""))
    config_data["last_llm_config_name"] = last_llm

    default_llm = config_data.get("default_llm_config_name")
    if default_llm not in llm_configs:
        default_llm = last_llm
    config_data["default_llm_config_name"] = default_llm

    embedding_configs = config_data["embedding_configs"]
    last_emb = config_data.get("last_embedding_config_name")
    if last_emb not in embedding_configs:
        last_emb = (DEFAULT_EMBEDDING_CONFIG_NAME
                    if DEFAULT_EMBEDDING_CONFIG_NAME in embedding_configs
                    else next(iter(embedding_configs), ""))
    config_data["last_embedding_config_name"] = last_emb

    return config_data


def create_config(config_file: str) -> dict:
    """创建默认配置文件并返回其内容。"""
    config = get_default_config()
    save_config(config, config_file)
    return config


def load_config(config_file: str) -> dict:
    """加载配置；文件不存在时先创建默认配置。"""
    if not os.path.exists(config_file):
        return create_config(config_file)
    try:
        with _config_lock:
            with open(config_file, "r", encoding="utf-8") as fh:
                return normalize_config(json.load(fh))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logging.error(f"配置文件格式错误: {e}")
        return normalize_config({})
    except (IOError, OSError) as e:
        logging.error(f"无法读取配置文件: {e}")
        return normalize_config({})


def save_config(config_data: dict, config_file: str) -> bool:
    """原子写入配置文件。"""
    try:
        with _config_lock:
            parent = os.path.dirname(os.path.abspath(config_file))
            os.makedirs(parent, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(suffix=".json", dir=parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(config_data, fh, ensure_ascii=False, indent=4)
                os.replace(temp_path, config_file)
            except Exception:
                os.unlink(temp_path)
                raise
        return True
    except (IOError, OSError) as e:
        logging.error(f"无法保存配置文件: {e}")
        return False


def get_workspace_root(config_data: dict) -> str:
    """workspace 根目录：配置为空时默认落在项目下的 workspace/。"""
    root = (config_data or {}).get("workspace_root", "").strip()
    if not root:
        root = os.path.join(APP_ROOT, "workspace")
    return os.path.abspath(root)


def get_llm_config(config_data: dict, name: str = None) -> dict:
    """取某个 LLM 配置；name 为空时优先取默认续写模型。"""
    configs = (config_data or {}).get("llm_configs", {})
    name = (name or (config_data or {}).get("default_llm_config_name")
            or (config_data or {}).get("last_llm_config_name"))
    if name not in configs:
        name = next(iter(configs), "")
    return dict(configs.get(name, {}))


def get_embedding_config(config_data: dict, name: str = None) -> dict:
    """取某个 Embedding 配置；name 为空时取“当前”配置。"""
    configs = (config_data or {}).get("embedding_configs", {})
    name = name or (config_data or {}).get("last_embedding_config_name")
    if name not in configs:
        name = next(iter(configs), "")
    return dict(configs.get(name, {}))


def apply_proxy(config_data: dict) -> None:
    """按配置设置/清除 HTTP(S)_PROXY 环境变量。"""
    proxy = (config_data or {}).get("proxy_setting", {}) or {}
    if proxy.get("enabled") and proxy.get("proxy_port"):
        url = f"http://{proxy.get('proxy_url', '127.0.0.1')}:{proxy['proxy_port']}"
        os.environ["HTTP_PROXY"] = url
        os.environ["HTTPS_PROXY"] = url
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


def test_llm_config(interface_format, api_key, base_url, model_name,
                    temperature, max_tokens, timeout, log_func,
                    handle_exception_func):
    """在后台线程测试 LLM 配置。"""
    def task():
        try:
            log_func("开始测试 LLM 配置...")
            from llm_adapters import create_llm_adapter
            adapter = create_llm_adapter(
                interface_format=interface_format,
                base_url=base_url,
                model_name=model_name,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            response = adapter.invoke("Please reply 'OK'")
            if response:
                log_func("✅ LLM 配置测试成功")
                log_func(f"测试回复: {response[:200]}")
            else:
                log_func("❌ LLM 配置测试失败：未获取到响应")
        except Exception as e:
            log_func(f"❌ LLM 配置测试出错: {e}")
            handle_exception_func("测试 LLM 配置时出错")

    threading.Thread(target=task, daemon=True).start()


def test_embedding_config(api_key, base_url, interface_format, model_name,
                          log_func, handle_exception_func):
    """在后台线程测试 Embedding 配置。"""
    def task():
        try:
            log_func("开始测试 Embedding 配置...")
            from embedding_adapters import create_embedding_adapter
            adapter = create_embedding_adapter(
                interface_format=interface_format,
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
            )
            vec = adapter.embed_query("测试文本")
            if vec:
                log_func("✅ Embedding 配置测试成功")
                log_func(f"向量维度: {len(vec)}")
            else:
                log_func("❌ Embedding 配置测试失败：未获取到向量")
        except Exception as e:
            log_func(f"❌ Embedding 配置测试出错: {e}")
            handle_exception_func("测试 Embedding 配置时出错")

    threading.Thread(target=task, daemon=True).start()
