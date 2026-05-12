FROM python:3.12-slim

WORKDIR /app

# সিস্টেম ডিপেন্ডেন্সি (যদি প্রয়োজন হয়)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ডিপেন্ডেন্সি প্রথমে কপি করুন (ক্যাশিং সুবিধা)
COPY requirements.txt .

# পাইথন প্যাকেজ ইনস্টল
RUN pip install --no-cache-dir -r requirements.txt

# বাকি ফাইল কপি
COPY . .

# বট চালানোর কমান্ড
CMD ["python", "main.py"]
