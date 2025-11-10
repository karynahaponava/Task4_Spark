import pyspark
from pyspark.sql import SparkSession
import sys
import os

JDBC_DRIVER_FILENAME = "postgresql-42.7.8.jar"

HADOOP_HOME_PATH = "C:\\hadoop"

def main():
    # === ПРИНУДИТЕЛЬНАЯ НАСТРОЙКА HADOOP ДЛЯ WINDOWS ===
    if os.name == 'nt':  # Проверка, что мы на Windows
        print(f"[ИНФО] Настройка окружения Hadoop для Windows...")
        
        # 1. Устанавливаем HADOOP_HOME для текущего процесса Python
        os.environ['HADOOP_HOME'] = HADOOP_HOME_PATH
        
        # 2. Проверяем наличие winutils.exe
        hadoop_bin = os.path.join(HADOOP_HOME_PATH, 'bin')
        winutils_path = os.path.join(hadoop_bin, 'winutils.exe')
        
        if not os.path.exists(winutils_path):
             print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Файл winutils.exe не найден по пути: {winutils_path}")
             sys.exit(1)
             
        # 3. Добавляем папку bin в системный PATH для текущего процесса
        os.environ['PATH'] = hadoop_bin + os.pathsep + os.environ['PATH']
        print(f"[ИНФО] HADOOP_HOME успешно установлен в: {os.environ['HADOOP_HOME']}")
        print(f"[ИНФО] winutils.exe найден в: {winutils_path}\n")

    # 1. Находим полный путь к JAR файлу драйвера PostgreSQL в текущей папке
    current_dir = os.getcwd()
    driver_path = os.path.join(current_dir, JDBC_DRIVER_FILENAME)

    # Проверка: если драйвер не найден, выводим понятную ошибку и выходим
    if not os.path.exists(driver_path):
        print(f"\n[ОШИБКА] Файл драйвера JDBC не найден: {driver_path}")
        sys.exit(1)

    print(f"[ИНФО] Используем JDBC драйвер: {driver_path}")

    # 2. Инициализация SparkSession
    # Мы передаем путь к драйверу прямо в конфигурацию Spark
    spark = SparkSession.builder \
        .appName("Pagila SQL Analysis") \
        .config("spark.jars", driver_path) \
        .config("spark.driver.extraClassPath", driver_path) \
        .getOrCreate()

    # Устанавливаем уровень логгирования WARN, чтобы не засорять вывод лишней информацией
    spark.sparkContext.setLogLevel("WARN")

    # --- Настройки подключения к базе данных Pagila в Docker ---
    # Эти параметры должны совпадать с теми, что указаны в вашем docker-compose.yml
    jdbc_url = "jdbc:postgresql://localhost:5432/postgres"
    db_properties = {
        "user": "postgres",
        "password": "123456",
        "driver": "org.postgresql.Driver"
    }

    # Список таблиц, которые нам понадобятся для запросов
    tables_to_load = [
        "actor", "category", "film", "film_actor", "film_category",
        "inventory", "rental", "payment", "customer", "address", "city"
    ]

    try:
        print("\n[ИНФО] Подключение к базе данных и загрузка таблиц...")
        for table in tables_to_load:
            # Читаем каждую таблицу из PostgreSQL и регистрируем ее как временное представление (View) в Spark.
            # Это позволяет нам потом писать к ним обычные SQL-запросы.
            spark.read.jdbc(url=jdbc_url, table=f"public.{table}", properties=db_properties) \
                 .createOrReplaceTempView(table)
        print("[ИНФО] Все таблицы успешно загружены и готовы к запросам!\n")

    except Exception as e:
        # Если подключение не удалось (например, Docker контейнер не запущен)
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Не удалось подключиться к базе данных.\nДетали ошибки: {e}")
        print("\nСОВЕТ: Проверьте, запущен ли ваш Docker контейнер с базой данных Pagila.")
        spark.stop()
        sys.exit(1)

    # Вспомогательная функция для выполнения SQL-запроса и красивого вывода результата
    def run_query(title, sql_query):
        print(f"--- {title} ---")
        # spark.sql(sql_query) выполняет запрос.
        # .show(truncate=False) выводит результат в консоль без обрезания длинных строк.
        spark.sql(sql_query).show(truncate=False)
        print("-" * 50 + "\n")

    # 1. Количество фильмов в каждой категории, отсортированное по убыванию.
    run_query("1. Количество фильмов в категориях", """
        SELECT name AS category_name, COUNT(film_category.film_id) AS film_count
            FROM category JOIN film_category ON category.category_id = film_category.category_id
            GROUP BY category.name
            ORDER BY film_count DESC
    """)

    # 2. 10 актеров, чьи фильмы арендовали больше всего, отсортированные по убыванию.
    run_query("2. Топ-10 актеров по числу аренд", """
        SELECT actor.first_name AS actor_first_name, actor.last_name AS actor_last_name, COUNT(rental.rental_id) AS rental_count
            FROM actor JOIN (film_actor JOIN (inventory JOIN
                rental ON inventory.inventory_id = rental.inventory_id) ON film_actor.film_id = inventory.film_id)
            ON actor.actor_id = film_actor.actor_id
        GROUP BY actor.actor_id, actor.first_name, actor.last_name
        ORDER BY rental_count DESC
        LIMIT 10
    """)

    # 3. Категория фильмов, на которую потратили больше всего денег.
    run_query("3. Самая прибыльная категория", """
        SELECT category.name AS category_name, SUM(payment.amount) AS total_spent
            FROM payment JOIN 
                (rental JOIN
                    (inventory JOIN 
	                    (film_category JOIN category ON film_category.category_id = category.category_id) 
	                    ON inventory.film_id = film_category.film_id) 
                    ON rental.inventory_id = inventory.inventory_id) 
                ON payment.rental_id = rental.rental_id
            GROUP BY category.name
            ORDER BY total_spent DESC
            LIMIT 1
    """)

    # 4. Названия фильмов, которых нет в инвентаре.
    run_query("4. Фильмы отсутствующие в инвентаре", """
        SELECT film.title
            FROM film
            WHERE
                NOT EXISTS (
                    SELECT 1
                        FROM inventory
                        WHERE inventory.film_id = film.film_id
   )
    """)

    # 5. Топ 3 актера, которые больше всего снимались в фильмах категории “Children”.
    run_query("5. Топ актеры в категории 'Children'", """
        SELECT first_name, last_name, film_count
            FROM (
                SELECT first_name, last_name, film_count, DENSE_RANK() OVER (ORDER BY film_count DESC) AS rnk
                    FROM (
                        SELECT actor.actor_id, actor.first_name, actor.last_name, COUNT(film_actor.film_id) AS film_count
                            FROM actor JOIN (film_actor JOIN (film_category JOIN category ON film_category.category_id = category.category_id) 
	                        ON film_actor.film_id = film_category.film_id) 
	                        ON actor.actor_id = film_actor.actor_id
                    WHERE category.name = 'Children'
                    GROUP BY actor.actor_id, actor.first_name, actor.last_name
                    ) AS actor_film_counts 
                ) AS ranked_actors 
            WHERE rnk <= 3
            ORDER BY film_count DESC, first_name
    """)

    # 6. Города с количеством активных и неактивных клиентов, сортировка по неактивным.
    run_query("6. Активные/неактивные клиенты по городам", """
        SELECT
            city.city,
            SUM(CASE WHEN cu.activebool = true THEN 1 ELSE 0 END) AS active_customers,
            SUM(CASE WHEN cu.activebool = false THEN 1 ELSE 0 END) AS inactive_customers
            FROM city
                JOIN address ON city.city_id = address.city_id
                JOIN customer cu ON address.address_id = cu.address_id
            GROUP BY city.city
            ORDER BY active_customers DESC, city.city
            LIMIT 20 
    """)

    # 7. Выведите категорию фильмов с наибольшим общим количеством часов проката в городах (адрес_клиента в этом городе), которые начинаются с буквы «a». Сделайте то же самое для городов с символом «-».
    run_query("7. Выведите категорию фильмов с наибольшим общим количеством часов проката в городах (адрес_клиента в этом городе), которые начинаются с буквы «a». Сделайте то же самое для городов с символом «-».", """
        WITH grouped_data AS (
  SELECT category.name AS category_name, CASE 
    WHEN city.city LIKE 'a%' THEN 'Starts with a'
    WHEN city.city LIKE '%-%' THEN 'Contains hyphen'
  END AS city_group,
  SUM(film.length) / 60.0 AS total_hours
    FROM (category JOIN (film_category JOIN (film JOIN (inventory JOIN (rental JOIN (customer JOIN (address JOIN city ON address.city_id = city.city_id)
                                                                                     ON customer.address_id = address.address_id)
                                                                        ON rental.customer_id = customer.customer_id)
                                                        ON inventory.inventory_id = rental.inventory_id)
                                            ON film.film_id = inventory.film_id)
                         ON film_category.film_id = film.film_id)
    ON category.category_id = film_category.category_id)
    WHERE city.city LIKE 'a%' OR city.city LIKE '%-%'
	GROUP BY city_group, category_name
), 
ranked_data AS (
  SELECT city_group, category_name, total_hours, DENSE_RANK() OVER (PARTITION BY city_group ORDER BY total_hours DESC) AS rnk
    FROM grouped_data
)

  SELECT city_group, category_name, total_hours
    FROM ranked_data
    WHERE rnk = 1
    ORDER BY city_group
    """)

    print("[ИНФО] Все запросы выполнены. Завершение работы Spark...")
    spark.stop()

if __name__ == "__main__":
    main()