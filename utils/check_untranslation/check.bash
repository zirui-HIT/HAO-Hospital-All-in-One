LANGUAGE=zh-Hans
python3 ./utils/check_untranslation/check.py \
    --root . \
    --out ./utils/check_untranslation/missing_${LANGUAGE}_ids.json \
    --lang $LANGUAGE
