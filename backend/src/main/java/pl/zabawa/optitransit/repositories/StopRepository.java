package pl.zabawa.optitransit.repositories;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import pl.zabawa.optitransit.models.entities.StopEntity;

import java.util.List;

@Repository
public interface StopRepository extends JpaRepository<StopEntity, String> {

    List<StopEntity> findByNameContainingIgnoreCase(String name, Pageable pageable);

}
