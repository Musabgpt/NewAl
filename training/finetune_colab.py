# Fine-tune the phone's coding model on what it learned (free Google Colab GPU).
#
# 1. In the app: 📈 التعلّم → تصدير بيانات التدريب → save newal_training.jsonl.
# 2. Open https://colab.research.google.com, Runtime → Change runtime type → T4 GPU.
# 3. Upload newal_training.jsonl, paste this file into a cell and run it.
# 4. Download qwen_newal-unsloth.Q4_K_M.gguf from the gguf_out folder and load it in the app
#    (model button → choose file), or assign it to the "coder" role in 🧠 النماذج.
#
# Each training example is a real task the agent solved on your phone (verified by Termux),
# so the model learns your kind of tasks and the output format the app expects.

# !pip install -q unsloth   (uncomment in Colab)

import json

from datasets import Dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel

BASE_MODEL = "unsloth/Qwen2.5-Coder-1.5B-Instruct"   # match the model you use on the phone
DATA_FILE = "newal_training.jsonl"
MAX_LEN = 4096

model, tokenizer = FastLanguageModel.from_pretrained(BASE_MODEL, max_seq_length=MAX_LEN, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth")

with open(DATA_FILE, encoding="utf-8") as f:
    rows = [json.loads(line) for line in f if line.strip()]
print(len(rows), "examples")

dataset = Dataset.from_list([
    {"text": tokenizer.apply_chat_template(r["messages"], tokenize=False)} for r in rows])

trainer = SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=dataset,
    args=SFTConfig(dataset_text_field="text", max_seq_length=MAX_LEN, per_device_train_batch_size=2,
                   gradient_accumulation_steps=4, num_train_epochs=2, learning_rate=2e-4,
                   logging_steps=5, output_dir="outputs", report_to="none"))
trainer.train()

# Merged model → GGUF (Q4_K_M) that llama.cpp in the app loads directly.
model.save_pretrained_gguf("gguf_out", tokenizer, quantization_method="q4_k_m")
print("done: see gguf_out/")
