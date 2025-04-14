import numpy as np
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import cosine_similarity
from neo4j_data_fetcher import InteractionsFetcher, Neo4jClient
import pandas as pd
from scipy.sparse import coo_matrix


class ItemBasedCF:
    def __init__(self, db_client: Neo4jClient, k_neighbors=5, min_sim=0.1, min_overlap=0):
        self.db_client = db_client
        self.k_neighbors = k_neighbors
        self.min_sim = min_sim
        self.min_overlap = min_overlap
        self.item_similarity = None
        self.user_item_matrix = None
        self.item_columns = None
        self.user_indices = None
        self.item_names_df = None

    def fit(self):
        fetcher = InteractionsFetcher(self.db_client)
        interactions = fetcher.fetch_interactions()
        self.item_names_df = interactions[['item', 'name', 'type']].drop_duplicates(subset=['item', 'type']).set_index(['item', 'type'])

        if interactions.empty:
            print("Warning: Interaction data is empty. Cannot fit the model.")
            return

        try:
            self.user_item_matrix = interactions.pivot_table(index="user", columns=["item", "type"], values="avg", fill_value=0, observed=True)
        except ValueError as e:
            print(f"Error during pivot_table: {e}")
            return

        if self.user_item_matrix.empty:
            print("Warning: User-item matrix is empty after pivoting.")
            return

        self.user_indices = self.user_item_matrix.index
        self.item_columns = self.user_item_matrix.columns

        matrix = self.user_item_matrix.T  # Transpose to make items as rows
        item_sparse = csr_matrix(matrix.values)

       
        similarity = cosine_similarity(item_sparse, dense_output=False)
        similarity = similarity.multiply(similarity > self.min_sim)

 
        binarized = item_sparse.copy()
        binarized.data = np.ones_like(binarized.data)
        overlap = binarized.dot(binarized.T)
        self.item_similarity = similarity.multiply(overlap > self.min_overlap)

    def recommend(self, user_id, top_n=5, item_type=None):
        if self.item_similarity is None or self.user_item_matrix is None:
            print("Model not fitted properly.")
            return pd.DataFrame(columns=['rank', 'item', 'type', 'name', 'score'])

        if user_id not in self.user_indices:
            print(f"User ID '{user_id}' not found.")
            return pd.DataFrame(columns=['rank', 'item', 'type', 'name', 'score'])

        user_vector = self.user_item_matrix.loc[user_id]
        scores = pd.Series(0, index=self.item_columns, dtype=float)

        for item_idx, rating in user_vector[user_vector > 0].items():
            item_index = self.item_columns.get_loc(item_idx)
            similar_items_scores = self.item_similarity[item_index].toarray().flatten()
            scores += rating * pd.Series(similar_items_scores, index=self.item_columns)

        # Filter already interacted
        interacted_items = user_vector[user_vector > 0].index
        scores = scores.drop(interacted_items, errors='ignore')

        if item_type:
            scores = scores[scores.index.get_level_values('type') == item_type]

        top_items = scores.nlargest(top_n)
        recommendations = []

        for rank, (item_id, item_type_rec) in enumerate(top_items.index, start=1):
            try:
                item_info = self.item_names_df.loc[(item_id, item_type_rec)]
                name = item_info['name']
            except KeyError:
                name = f"Unknown {item_type_rec} ({item_id})"
            recommendations.append({
                'rank': rank,
                'item': item_id,
                'type': item_type_rec,
                'name': name,
                'score': top_items[(item_id, item_type_rec)]
            })

        return pd.DataFrame(recommendations)[['rank', 'item', 'type', 'name', 'score']]


if __name__ == "__main__":
    db_client = Neo4jClient()
    model = ItemBasedCF(db_client, k_neighbors=25, min_sim=0.001, min_overlap=0)
    model.fit()
    
    user_ids = [
        '633af53b-f78c-474c-9324-2a734bd86d24',
        '65ab857a-6ff4-493f-aa8d-ddde6463cc20',
        '72effc5b-589a-4076-9be5-f7c3d8533f70',
        '8aaafb9e-0f60-47d1-9b98-1b171564fbf9',
        'b9c32bc3-4b7f-46fd-af3b-ca48060b89a1',
        '3738e035-45a5-4b8b-86a2-32ff64a76f03',
        '82f642dc-fda0-46ed-b080-f4b1866899a6',
        'b99c49fc-f7b1-4cd4-8d22-cc5b8575f07f',
        '3989ed58-1cce-45e2-9b5b-e4827165e324'
    ]

    for uid in user_ids:
        print(f"User: {uid}")
        print("Top Trips:")
        print(model.recommend(uid, top_n=3, item_type='Trip'))
        print("Top Events:")
        print(model.recommend(uid, top_n=3, item_type='Event'))
        print("Top Destinations:")
        print(model.recommend(uid, top_n=3, item_type='Destination'))

    db_client.close()
