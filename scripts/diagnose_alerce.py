
from alerce.core import Alerce
client = Alerce()

# Try without time filter - just get recent SNIa
objects = client.query_objects(
    classifier='lc_classifier',
    class_name='SNIa',
    format='pandas',
    page_size=10,
    probability=0.5
)
print(f'SNIa found: {len(objects) if objects is not None else 0}')
if objects is not None and len(objects) > 0:
    print(objects[['oid','meanra','meandec','ndet']].head())
