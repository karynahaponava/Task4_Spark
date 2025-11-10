import pyspark
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, desc, count, sum, lit, when, unix_timestamp, round, dense_rank
)
from pyspark.sql.window import Window

def main():
    # 1. Инициализация SparkSession
    spark = SparkSession.builder \
        .appName("Sakila DB Queries") \
        .config("spark.sql.legacy.timeParserPolicy", "LEGACY") \
        .getOrCreate()

    # --- 2. Загрузка данных (Замените 'path/to/' на ваш путь к CSV файлам) ---
    # Для простоты предполагается, что CSV-файлы имеют заголовки.
    # Если у вас другой формат (например, Parquet), используйте spark.read.parquet()
    
    try:
        base_path = "path/to/your/csv/files/" # <-- ОБНОВИТЕ ЭТОТ ПУТЬ

        actor_df = spark.read.csv(f"{base_path}actor.csv", header=True, inferSchema=True)
        category_df = spark.read.csv(f"{base_path}category.csv", header=True, inferSchema=True)
        film_df = spark.read.csv(f"{base_path}film.csv", header=True, inferSchema=True)
        film_actor_df = spark.read.csv(f"{base_path}film_actor.csv", header=True, inferSchema=True)
        film_category_df = spark.read.csv(f"{base_path}film_category.csv", header=True, inferSchema=True)
        inventory_df = spark.read.csv(f"{base_path}inventory.csv", header=True, inferSchema=True)
        rental_df = spark.read.csv(f"{base_path}rental.csv", header=True, inferSchema=True, timestampFormat="yyyy-MM-dd HH:mm:ss")
        payment_df = spark.read.csv(f"{base_path}payment.csv", header=True, inferSchema=True)
        customer_df = spark.read.csv(f"{base_path}customer.csv", header=True, inferSchema=True)
        address_df = spark.read.csv(f"{base_path}address.csv", header=True, inferSchema=True)
        city_df = spark.read.csv(f"{base_path}city.csv", header=True, inferSchema=True)

        # Создание временных представлений (Temp Views) для удобства SQL-подобных операций
        actor_df.createOrReplaceTempView("actor")
        category_df.createOrReplaceTempView("category")
        film_df.createOrReplaceTempView("film")
        film_actor_df.createOrReplaceTempView("film_actor")
        film_category_df.createOrReplaceTempView("film_category")
        inventory_df.createOrReplaceTempView("inventory")
        rental_df.createOrReplaceTempView("rental")
        payment_df.createOrReplaceTempView("payment")
        customer_df.createOrReplaceTempView("customer")
        address_df.createOrReplaceTempView("address")
        city_df.createOrReplaceTempView("city")

    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        print("Пожалуйста, убедитесь, что путь 'path/to/your/csv/files/' указан верно и файлы существуют.")
        spark.stop()
        return

    # --- 3. Выполнение запросов ---

    print("--- Запрос 1: Количество фильмов в каждой категории (по убыванию) ---")
    query1_df = film_category_df.join(category_df, "category_id") \
        .groupBy("name") \
        .agg(count("film_id").alias("movie_count")) \
        .orderBy(desc("movie_count"))
    query1_df.show()

    print("\n--- Запрос 2: 10 актеров, фильмы которых принесли больше всего денег (по убыванию) ---")
    query2_df = payment_df.join(rental_df, "rental_id") \
        .join(inventory_df, "inventory_id") \
        .join(film_actor_df, "film_id") \
        .join(actor_df, "actor_id") \
        .groupBy("actor.actor_id", "actor.first_name", "actor.last_name") \
        .agg(round(sum("amount"), 2).alias("total_revenue")) \
        .orderBy(desc("total_revenue")) \
        .limit(10)
    query2_df.show()

    print("\n--- Запрос 3: Категория фильмов, на которую было потрачено больше всего денег ---")
    query3_df = payment_df.join(rental_df, "rental_id") \
        .join(inventory_df, "inventory_id") \
        .join(film_category_df, "film_id") \
        .join(category_df, "category_id") \
        .groupBy("category.name") \
        .agg(round(sum("amount"), 2).alias("total_spent")) \
        .orderBy(desc("total_spent")) \
        .limit(1)
    query3_df.show()

    print("\n--- Запрос 4: Названия фильмов, которых нет в inventory ---")
    # Используем left_anti join, чтобы найти фильмы, у которых нет соответствия в inventory
    query4_df = film_df.join(inventory_df, "film_id", "left_anti") \
        .select("title")
    query4_df.show()

    print("\n--- Запрос 5: Топ 3 актера в категории 'Children' (с учетом одинаковых мест) ---")
    # Находим актеров в категории "Children"
    children_actors_df = category_df.filter(col("name") == "Children") \
        .join(film_category_df, "category_id") \
        .join(film_actor_df, "film_id") \
        .join(actor_df, "actor_id") \
        .groupBy("actor.actor_id", "actor.first_name", "actor.last_name") \
        .agg(count("film_actor.film_id").alias("movie_count"))

    # Ранжируем актеров, используя dense_rank для обработки одинаковых значений
    window_spec = Window.orderBy(desc("movie_count"))
    query5_df = children_actors_df.withColumn("rank", dense_rank().over(window_spec)) \
        .filter(col("rank") <= 3) \
        .select("first_name", "last_name", "movie_count", "rank")
    
    query5_df.show()

    print("\n--- Запрос 6: Города с количеством активных и неактивных клиентов ---")
    # (active - customer.active = 1)
    query6_df = customer_df.join(address_df, "address_id") \
        .join(city_df, "city_id") \
        .groupBy("city.city") \
        .agg(
            count(when(col("active") == 1, True)).alias("active_customers"),
            count(when(col("active") == 0, True)).alias("inactive_customers")
        ) \
        .orderBy(desc("inactive_customers"))
    query6_df.show()


    print("\n--- Запрос 7: Категория с макс. часами аренды (Города на 'a' и Города с '-') ---")
    
    # Сначала рассчитаем длительность аренды в часах
    rental_with_duration_df = rental_df.filter(col("return_date").isNotNull()) \
        .withColumn("rental_duration_hours", 
            (unix_timestamp("return_date") - unix_timestamp("rental_date")) / 3600
        )

    # Общая база для джойнов
    base_df = city_df.join(address_df, "city_id") \
        .join(customer_df, "address_id") \
        .join(rental_with_duration_df, "customer_id") \
        .join(inventory_df, "inventory_id") \
        .join(film_category_df, "film_id") \
        .join(category_df, "category_id")

    # Часть A: Города, начинающиеся на 'a'
    print("Часть A: Города на 'a'")
    query7a_df = base_df.filter(col("city.city").like("a%")) \
        .groupBy("category.name") \
        .agg(round(sum("rental_duration_hours"), 2).alias("total_rental_hours")) \
        .orderBy(desc("total_rental_hours")) \
        .limit(1)
    query7a_df.show()

    # Часть B: Города, содержащие '-'
    print("Часть B: Города с '-'")
    query7b_df = base_df.filter(col("city.city").like("%-%")) \
        .groupBy("category.name") \
        .agg(round(sum("rental_duration_hours"), 2).alias("total_rental_hours")) \
        .orderBy(desc("total_rental_hours")) \
        .limit(1)
    query7b_df.show()


    # 4. Остановка сессии
    print("Запросы выполнены. Остановка Spark сессии.")
    spark.stop()

if __name__ == "__main__":
    main()